// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20 {
    function transfer(address to, uint256 value) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @title CampaignEscrow — per-deal escrow for ZyroXQ influencer campaigns.
/// @notice HSP settles the advertiser's Payment Mandate by transferring tokens straight to this
///         contract (EIP-3009 `transferWithAuthorization`), so funding is detected from the
///         unallocated balance rather than a deposit call. Payouts require an attestation signed
///         by the proof verifier; the operator can never pay out on its own.
contract CampaignEscrow {
    enum State { None, Registered, Funded, Disputed, Closed }

    struct Deal {
        address advertiser;
        address payee;
        address token;
        uint256 cap;
        uint256 released;
        uint64 proofDeadline;
        State state;
    }

    address public immutable operator;
    address public immutable verifier;
    address public immutable arbiter;

    mapping(bytes32 => Deal) public deals;
    /// @notice Per token: sum of unreleased caps of funded deals. balance - allocated = unallocated.
    mapping(address => uint256) public allocated;
    mapping(bytes32 => bool) public usedAttestations;

    event DealRegistered(bytes32 indexed dealId, address advertiser, address payee, address token, uint256 cap);
    event DealFunded(bytes32 indexed dealId, uint256 cap);
    event Released(bytes32 indexed dealId, address payee, uint256 amount, uint256 nonce);
    event Refunded(bytes32 indexed dealId, address advertiser, uint256 amount);
    event Disputed(bytes32 indexed dealId);
    event Resolved(bytes32 indexed dealId, uint256 toPayee, uint256 toAdvertiser);

    error NotAuthorized();
    error BadState();
    error InsufficientFunding();
    error ExceedsCap();
    error BadAttestation();
    error TooEarly();
    error TransferFailed();

    constructor(address operator_, address verifier_, address arbiter_) {
        operator = operator_;
        verifier = verifier_;
        arbiter = arbiter_;
    }

    modifier only(address who) {
        if (msg.sender != who) revert NotAuthorized();
        _;
    }

    function registerDeal(
        bytes32 dealId,
        address advertiser,
        address payee,
        address token,
        uint256 cap,
        uint64 proofDeadline
    ) external only(operator) {
        if (deals[dealId].state != State.None) revert BadState();
        deals[dealId] = Deal(advertiser, payee, token, cap, 0, proofDeadline, State.Registered);
        emit DealRegistered(dealId, advertiser, payee, token, cap);
    }

    /// @notice Called after HSP reports the payment finalized; checks the tokens actually arrived.
    function markFunded(bytes32 dealId) external only(operator) {
        Deal storage d = deals[dealId];
        if (d.state != State.Registered) revert BadState();
        if (IERC20(d.token).balanceOf(address(this)) - allocated[d.token] < d.cap) revert InsufficientFunding();
        allocated[d.token] += d.cap;
        d.state = State.Funded;
        emit DealFunded(dealId, d.cap);
    }

    function attestationDigest(bytes32 dealId, address payee, uint256 amount, uint256 nonce) public view returns (bytes32) {
        return keccak256(abi.encode(address(this), block.chainid, dealId, payee, amount, nonce));
    }

    /// @notice Pays `amount` to the deal's payee if the verifier signed exactly this release.
    function release(bytes32 dealId, uint256 amount, uint256 nonce, bytes calldata signature) external {
        Deal storage d = deals[dealId];
        if (d.state != State.Funded) revert BadState();
        if (d.released + amount > d.cap) revert ExceedsCap();
        bytes32 digest = attestationDigest(dealId, d.payee, amount, nonce);
        if (usedAttestations[digest]) revert BadAttestation();
        if (_recover(_ethSigned(digest), signature) != verifier) revert BadAttestation();
        usedAttestations[digest] = true;
        d.released += amount;
        allocated[d.token] -= amount;
        _transfer(d.token, d.payee, amount);
        emit Released(dealId, d.payee, amount, nonce);
    }

    /// @notice Operator closes a funded deal after the final payout; the remainder goes back to the advertiser.
    function close(bytes32 dealId) external only(operator) {
        _refundRemainder(dealId);
    }

    /// @notice Anyone can trigger a full refund if no proof was paid out by the deadline.
    function refundExpired(bytes32 dealId) external {
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

    function resolve(bytes32 dealId, uint256 toPayee) external only(arbiter) {
        Deal storage d = deals[dealId];
        if (d.state != State.Disputed) revert BadState();
        uint256 remaining = d.cap - d.released;
        if (toPayee > remaining) revert ExceedsCap();
        d.released += toPayee;
        d.state = State.Closed;
        allocated[d.token] -= remaining;
        if (toPayee > 0) _transfer(d.token, d.payee, toPayee);
        if (remaining - toPayee > 0) _transfer(d.token, d.advertiser, remaining - toPayee);
        emit Resolved(dealId, toPayee, remaining - toPayee);
    }

    function _refundRemainder(bytes32 dealId) private {
        Deal storage d = deals[dealId];
        if (d.state != State.Funded) revert BadState();
        uint256 remaining = d.cap - d.released;
        d.state = State.Closed;
        allocated[d.token] -= remaining;
        if (remaining > 0) _transfer(d.token, d.advertiser, remaining);
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
    function _transfer(address token, address to, uint256 amount) private {
        (bool ok, bytes memory data) = token.call(abi.encodeCall(IERC20.transfer, (to, amount)));
        if (!ok || (data.length != 0 && !abi.decode(data, (bool)))) revert TransferFailed();
    }
}
