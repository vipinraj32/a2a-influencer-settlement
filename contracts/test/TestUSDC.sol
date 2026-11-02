// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title TestUSDC — TESTS ONLY. A minimal EIP-3009 token shaped like Circle's FiatToken (6 decimals, EIP-712
///        domain name/version, transferWithAuthorization with v/r/s, authorizationState), so the escrow and the
///        sepolia payment path run end to end on a local chain. Never deployed to a public network.
contract TestUSDC {
    string public name;
    string public constant version = "2";
    string public constant symbol = "USDC";
    uint8 public constant decimals = 6;
    uint256 public totalSupply;
    bytes32 public immutable DOMAIN_SEPARATOR;
    bytes32 public constant TRANSFER_WITH_AUTHORIZATION_TYPEHASH =
        keccak256("TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)");

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(bytes32 => bool)) public authorizationState;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event AuthorizationUsed(address indexed authorizer, bytes32 indexed nonce);

    constructor(string memory name_) {
        name = name_;
        DOMAIN_SEPARATOR = keccak256(abi.encode(
            keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
            keccak256(bytes(name_)), keccak256(bytes(version)), block.chainid, address(this)
        ));
    }

    function mint(address to, uint256 value) external {
        balanceOf[to] += value;
        totalSupply += value;
        emit Transfer(address(0), to, value);
    }

    function transfer(address to, uint256 value) external returns (bool) {
        _move(msg.sender, to, value);
        return true;
    }

    function transferWithAuthorization(address from, address to, uint256 value, uint256 validAfter, uint256 validBefore, bytes32 nonce,
                                       uint8 v, bytes32 r, bytes32 s) external {
        require(block.timestamp > validAfter, "authorization is not yet valid");
        require(block.timestamp < validBefore, "authorization is expired");
        require(!authorizationState[from][nonce], "authorization is used");
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR,
            keccak256(abi.encode(TRANSFER_WITH_AUTHORIZATION_TYPEHASH, from, to, value, validAfter, validBefore, nonce))));
        address signer = ecrecover(digest, v, r, s);
        require(signer != address(0) && signer == from, "invalid signature");
        authorizationState[from][nonce] = true;
        emit AuthorizationUsed(from, nonce);
        _move(from, to, value);
    }

    function _move(address from, address to, uint256 value) private {
        require(balanceOf[from] >= value, "transfer amount exceeds balance");
        balanceOf[from] -= value;
        balanceOf[to] += value;
        emit Transfer(from, to, value);
    }
}
