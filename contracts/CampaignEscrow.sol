// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20 {
    function transfer(address to, uint256 value) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @title CampaignEscrow — per-deal escrow for ZyroXQ influencer campaigns.
/// @notice HSP settles the advertiser's Payment Mandate by transferring tokens straight to this
///         contract (EIP-3009 `transferWithAuthorization` on the token), so there is no deposit call
///         that names a deal. Attribution is therefore explicit: the operator verifies the settlement
///         transaction's receipt off-chain (Transfer payer → this contract, exact cap, correct token)
///         and binds that transaction hash to exactly one deal in `markFunded`. A funding transaction
///         can be bound once, and the unallocated balance must still cover the cap (solvency backstop).
///         Payouts require an attestation signed by the proof verifier; the operator can never pay out
///         on its own, and money only ever goes to the deal's payee or back to its advertiser.
contract CampaignEscrow {
    enum State { None, Registered, Funded, Disputed, Closed }

    struct Deal {
        address advertiser;
        address payee;
        uint256 cap;
        uint256 released;
        uint64 proofDeadline;
        State state;
        bytes32 fundingTx;
    }

    address public immutable operator;
    address public immutable verifier;
    address public immutable arbiter;
    /// @notice The only token this escrow accepts (USDC on HashKey Chain).
    address public immutable token;

    mapping(bytes32 => Deal) public deals;
    /// @notice Sum of unreleased caps of funded deals. balance - allocated = unallocated.
    uint256 public allocated;
    mapping(bytes32 => bool) public usedAttestations;
    /// @notice Settlement transactions already bound to a deal.
    mapping(bytes32 => bool) public usedFundingTx;

    uint256 private _entered = 1;

    event DealRegistered(bytes32 indexed dealId, address advertiser, address payee, uint256 cap);
    event DealFunded(bytes32 indexed dealId, uint256 cap, bytes32 fundingTx);
    event Released(bytes32 indexed dealId, address payee, uint256 amount, uint256 nonce);
    event Refunded(bytes32 indexed dealId, address advertiser, uint256 amount);
    event Disputed(bytes32 indexed dealId);
    event Resolved(bytes32 indexed dealId, uint256 toPayee, uint256 toAdvertiser);

    error NotAuthorized();
    error BadState();
    error BadDeal();
    error InsufficientFunding();
    error FundingTxUsed();
    error ExceedsCap();
    error BadAttestation();
    error TooEarly();
    error TransferFailed();
    error Reentrancy();

    constructor(address operator_, address verifier_, address arbiter_, address token_) {
        if (operator_ == address(0) || verifier_ == address(0) || arbiter_ == address(0) || token_ == address(0)) revert BadDeal();
        operator = operator_;
        verifier = verifier_;
        arbiter = arbiter_;
        token = token_;
    }

    modifier only(address who) {
        if (msg.sender != who) revert NotAuthorized();
        _;
    }

    modifier nonReentrant() {
        if (_entered != 1) revert Reentrancy();
        _entered = 2;
        _;
        _entered = 1;
    }

    function registerDeal(bytes32 dealId, address advertiser, address payee, uint256 cap, uint64 proofDeadline) external only(operator) {
        if (deals[dealId].state != State.None) revert BadState();
        if (advertiser == address(0) || payee == address(0) || cap == 0) revert BadDeal();
        deals[dealId] = Deal(advertiser, payee, cap, 0, proofDeadline, State.Registered, bytes32(0));
        emit DealRegistered(dealId, advertiser, payee, cap);
    }

    /// @notice Binds a verified settlement transaction to one deal and locks its cap.
    /// @param fundingTx Hash of the HSP settlement transaction whose receipt the operator verified.
    function markFunded(bytes32 dealId, bytes32 fundingTx) external only(operator) {
        Deal storage d = deals[dealId];
        if (d.state != State.Registered) revert BadState();
        if (fundingTx == bytes32(0) || usedFundingTx[fundingTx]) revert FundingTxUsed();
        if (IERC20(token).balanceOf(address(this)) - allocated < d.cap) revert InsufficientFunding();
        usedFundingTx[fundingTx] = true;
        allocated += d.cap;
        d.fundingTx = fundingTx;
        d.state = State.Funded;
        emit DealFunded(dealId, d.cap, fundingTx);
    }

    function attestationDigest(bytes32 dealId, address payee, uint256 amount, uint256 nonce) public view returns (bytes32) {
        return keccak256(abi.encode(address(this), block.chainid, dealId, payee, amount, nonce));
    }

    /// @notice Pays `amount` to the deal's payee if the verifier signed exactly this release.
    function release(bytes32 dealId, uint256 amount, uint256 nonce, bytes calldata signature) external nonReentrant {
        Deal storage d = deals[dealId];
        if (d.state != State.Funded) revert BadState();
        if (amount == 0 || d.released + amount > d.cap) revert ExceedsCap();
        bytes32 digest = attestationDigest(dealId, d.payee, amount, nonce);
        if (usedAttestations[digest]) revert BadAttestation();
        if (_recover(_ethSigned(digest), signature) != verifier) revert BadAttestation();
        usedAttestations[digest] = true;
        d.released += amount;
        allocated -= amount;
        _transfer(d.payee, amount);
        emit Released(dealId, d.payee, amount, nonce);
    }

    /// @notice Operator closes a funded deal after the final payout; the remainder goes back to the advertiser.
    function close(bytes32 dealId) external only(operator) nonReentrant {
        _refundRemainder(dealId);
    }

    /// @notice Anyone can trigger a full refund if no proof was paid out by the deadline.
    function refundExpired(bytes32 dealId) external nonReentrant {
        Deal storage d = deals[dealId];
        if (d.released != 0 || block.timestamp <= d.proofDeadline) revert TooEarly();
        _refundRemainder(dealId);
    }

    function dispute(bytes32 dealId) external {
        Deal storage d = deals[dealId];
        if (msg.sender != d.advertiser) revert NotAuthorized();
        if (d.state != State.Funded) revert BadState();
        d.state = State.Disputed;
        emit Disputed(dealId);
    }

    function resolve(bytes32 dealId, uint256 toPayee) external only(arbiter) nonReentrant {
        Deal storage d = deals[dealId];
        if (d.state != State.Disputed) revert BadState();
        uint256 remaining = d.cap - d.released;
        if (toPayee > remaining) revert ExceedsCap();
        d.released += toPayee;
        d.state = State.Closed;
        allocated -= remaining;
        if (toPayee > 0) _transfer(d.payee, toPayee);
        if (remaining - toPayee > 0) _transfer(d.advertiser, remaining - toPayee);
        emit Resolved(dealId, toPayee, remaining - toPayee);
    }

    function _refundRemainder(bytes32 dealId) private {
        Deal storage d = deals[dealId];
        if (d.state != State.Funded) revert BadState();
        uint256 remaining = d.cap - d.released;
        d.state = State.Closed;
        allocated -= remaining;
        if (remaining > 0) _transfer(d.advertiser, remaining);
        emit Refunded(dealId, d.advertiser, remaining);
    }

    function _ethSigned(bytes32 digest) private pure returns (bytes32) {
        return keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", digest));
    }

    function _recover(bytes32 hash, bytes calldata sig) private pure returns (address) {
        if (sig.length != 65) return address(0);
        bytes32 r = bytes32(sig[0:32]);
        bytes32 s = bytes32(sig[32:64]);
        uint8 v = uint8(sig[64]);
        // Reject malleable high-s signatures
        if (uint256(s) > 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0) return address(0);
        if (v != 27 && v != 28) return address(0);
        return ecrecover(hash, v, r, s);
    }

    /// @dev Tolerates tokens that return no value (e.g. USDT).
    function _transfer(address to, uint256 amount) private {
        (bool ok, bytes memory data) = token.call(abi.encodeCall(IERC20.transfer, (to, amount)));
        if (!ok || (data.length != 0 && !abi.decode(data, (bool)))) revert TransferFailed();
    }
}
