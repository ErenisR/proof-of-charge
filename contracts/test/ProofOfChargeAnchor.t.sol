// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "../src/ProofOfChargeAnchor.sol";

interface Vm {
    function expectRevert(bytes calldata revertData) external;
    function prank(address msgSender) external;
    function warp(uint256 newTimestamp) external;
}

contract ProofOfChargeAnchorTest {
    Vm private constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    ProofOfChargeAnchor private anchor;

    address private constant NON_OWNER = address(0xBEEF);
    address private constant NEW_OWNER = address(0xCAFE);
    bytes32 private constant BATCH_ROOT = 0x7a635c918099f10c2ac0ed1347278badfbc04300f270f9410e89dae1f149b88d;

    function setUp() public {
        anchor = new ProofOfChargeAnchor();
    }

    function testConstructorSetsOwner() public view {
        _assertEq(anchor.owner(), address(this), "owner");
    }

    function testOwnerCanAnchorAndReadBatch() public {
        vm.warp(1_780_000_000);

        anchor.anchorBatch("2026-06-12", "chain_demo", BATCH_ROOT, 2);

        (bytes32 batchRoot, uint256 receiptCount, address operator, uint256 timestamp) =
            anchor.getAnchor("2026-06-12", "chain_demo");

        _assertEq(batchRoot, BATCH_ROOT, "batchRoot");
        _assertEq(receiptCount, 2, "receiptCount");
        _assertEq(operator, address(this), "operator");
        _assertEq(timestamp, 1_780_000_000, "timestamp");
    }

    function testNonOwnerCannotAnchor() public {
        vm.expectRevert(abi.encodeWithSelector(ProofOfChargeAnchor.NotOwner.selector));
        vm.prank(NON_OWNER);

        anchor.anchorBatch("2026-06-12", "chain_demo", BATCH_ROOT, 2);
    }

    function testCannotAnchorDuplicateDayAndPrefix() public {
        anchor.anchorBatch("2026-06-12", "chain_demo", BATCH_ROOT, 2);

        bytes32 anchorId = anchor.computeAnchorId("2026-06-12", "chain_demo");
        vm.expectRevert(abi.encodeWithSelector(ProofOfChargeAnchor.AnchorAlreadyExists.selector, anchorId));

        anchor.anchorBatch("2026-06-12", "chain_demo", BATCH_ROOT, 2);
    }

    function testCanAnchorSameDayWithDifferentPrefix() public {
        bytes32 secondRoot = 0x8a635c918099f10c2ac0ed1347278badfbc04300f270f9410e89dae1f149b88d;

        anchor.anchorBatch("2026-06-12", "chain_demo_a", BATCH_ROOT, 2);
        anchor.anchorBatch("2026-06-12", "chain_demo_b", secondRoot, 3);

        (bytes32 firstBatchRoot, uint256 firstCount,,) = anchor.getAnchor("2026-06-12", "chain_demo_a");
        (bytes32 secondBatchRoot, uint256 secondCount,,) = anchor.getAnchor("2026-06-12", "chain_demo_b");

        _assertEq(firstBatchRoot, BATCH_ROOT, "firstBatchRoot");
        _assertEq(firstCount, 2, "firstCount");
        _assertEq(secondBatchRoot, secondRoot, "secondBatchRoot");
        _assertEq(secondCount, 3, "secondCount");
    }

    function testRejectsZeroBatchRoot() public {
        vm.expectRevert(abi.encodeWithSelector(ProofOfChargeAnchor.InvalidBatchRoot.selector));

        anchor.anchorBatch("2026-06-12", "chain_demo", bytes32(0), 2);
    }

    function testRejectsZeroReceiptCount() public {
        vm.expectRevert(abi.encodeWithSelector(ProofOfChargeAnchor.InvalidReceiptCount.selector));

        anchor.anchorBatch("2026-06-12", "chain_demo", BATCH_ROOT, 0);
    }

    function testMissingAnchorReverts() public {
        bytes32 anchorId = anchor.computeAnchorId("2026-06-12", "missing");
        vm.expectRevert(abi.encodeWithSelector(ProofOfChargeAnchor.AnchorNotFound.selector, anchorId));

        anchor.getAnchor("2026-06-12", "missing");
    }

    function testOwnerCanTransferOwnership() public {
        anchor.transferOwnership(NEW_OWNER);

        _assertEq(anchor.owner(), NEW_OWNER, "owner");
    }

    function testNonOwnerCannotTransferOwnership() public {
        vm.expectRevert(abi.encodeWithSelector(ProofOfChargeAnchor.NotOwner.selector));
        vm.prank(NON_OWNER);

        anchor.transferOwnership(NEW_OWNER);
    }

    function testRejectsZeroAddressOwner() public {
        vm.expectRevert(abi.encodeWithSelector(ProofOfChargeAnchor.InvalidOwner.selector));

        anchor.transferOwnership(address(0));
    }

    function _assertEq(address actual, address expected, string memory label) private pure {
        require(actual == expected, label);
    }

    function _assertEq(bytes32 actual, bytes32 expected, string memory label) private pure {
        require(actual == expected, label);
    }

    function _assertEq(uint256 actual, uint256 expected, string memory label) private pure {
        require(actual == expected, label);
    }
}

