// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title ProofOfChargeAnchor
/// @notice Stores daily Proof-of-Charge Merkle batch roots on-chain.
/// @dev The contract anchors batch roots only. Receipt data stays off-chain.
contract ProofOfChargeAnchor {
    struct BatchAnchor {
        bytes32 batchRoot;
        uint256 receiptCount;
        address operator;
        uint256 timestamp;
        bool exists;
    }

    address public owner;

    mapping(bytes32 => BatchAnchor) private anchors;

    event BatchAnchored(
        string day,
        string sessionPrefix,
        bytes32 indexed batchRoot,
        uint256 receiptCount,
        address indexed operator,
        uint256 timestamp
    );

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    error NotOwner();
    error InvalidBatchRoot();
    error InvalidReceiptCount();
    error AnchorAlreadyExists(bytes32 anchorId);
    error AnchorNotFound(bytes32 anchorId);
    error InvalidOwner();

    modifier onlyOwner() {
        if (msg.sender != owner) {
            revert NotOwner();
        }
        _;
    }

    constructor() {
        owner = msg.sender;
        emit OwnershipTransferred(address(0), msg.sender);
    }

    /// @notice Anchor a daily batch root for a session group.
    /// @param day Calendar day in YYYY-MM-DD format.
    /// @param sessionPrefix Optional backend session prefix. Use an empty string for all sessions.
    /// @param batchRoot Merkle root computed from receipt hashes.
    /// @param receiptCount Number of receipts included in the anchored batch.
    function anchorBatch(
        string calldata day,
        string calldata sessionPrefix,
        bytes32 batchRoot,
        uint256 receiptCount
    ) external onlyOwner {
        if (batchRoot == bytes32(0)) {
            revert InvalidBatchRoot();
        }
        if (receiptCount == 0) {
            revert InvalidReceiptCount();
        }

        bytes32 anchorId = computeAnchorId(day, sessionPrefix);
        if (anchors[anchorId].exists) {
            revert AnchorAlreadyExists(anchorId);
        }

        anchors[anchorId] = BatchAnchor({
            batchRoot: batchRoot,
            receiptCount: receiptCount,
            operator: msg.sender,
            timestamp: block.timestamp,
            exists: true
        });

        emit BatchAnchored(day, sessionPrefix, batchRoot, receiptCount, msg.sender, block.timestamp);
    }

    /// @notice Read the stored anchor for a day and prefix.
    function getAnchor(
        string calldata day,
        string calldata sessionPrefix
    )
        external
        view
        returns (
            bytes32 batchRoot,
            uint256 receiptCount,
            address operator,
            uint256 timestamp
        )
    {
        bytes32 anchorId = computeAnchorId(day, sessionPrefix);
        BatchAnchor memory anchor = anchors[anchorId];
        if (!anchor.exists) {
            revert AnchorNotFound(anchorId);
        }
        return (anchor.batchRoot, anchor.receiptCount, anchor.operator, anchor.timestamp);
    }

    /// @notice Compute the storage key used for a day and prefix.
    function computeAnchorId(string calldata day, string calldata sessionPrefix) public pure returns (bytes32) {
        return keccak256(abi.encode(day, sessionPrefix));
    }

    /// @notice Transfer anchoring permission to another operator.
    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) {
            revert InvalidOwner();
        }
        address previousOwner = owner;
        owner = newOwner;
        emit OwnershipTransferred(previousOwner, newOwner);
    }
}

