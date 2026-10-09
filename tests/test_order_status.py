"""订单状态分类的测试。

背景：`order-list` 会返回**未支付**订单。实测账号里就有 1 条「待支付」。
若直接计入热量合计，会把还没吃到的东西算成已摄入热量——这是本项目
必须防住的错误。
"""

from __future__ import annotations

import unittest

from fixtures import ORDERS, nutrition_payload

from mcd_order_calories.matching import AliasTable, ProductMatcher
from mcd_order_calories.nutrition import parse_nutrition
from mcd_order_calories.orders import (
    DEFAULT_STATUS_POLICY,
    OrderStatusPolicy,
    compute_order,
    compute_orders,
)


def build_matcher() -> ProductMatcher:
    entries, _ = parse_nutrition(nutrition_payload())
    return ProductMatcher(entries, AliasTable())


def order_with(status: str, order_id: str = "X") -> dict:
    return ORDERS[0] | {"orderId": order_id, "orderStatus": status}


class TestClassification(unittest.TestCase):
    def test_consumed_status_is_counted(self) -> None:
        self.assertEqual(DEFAULT_STATUS_POLICY.classify("订单已完成"), "consumed")

    def test_pending_status_is_not_counted(self) -> None:
        self.assertEqual(DEFAULT_STATUS_POLICY.classify("待支付"), "pending")

    def test_cancelled_status_is_not_counted(self) -> None:
        for status in ("已取消", "已退款", "支付失败"):
            with self.subTest(status=status):
                self.assertEqual(DEFAULT_STATUS_POLICY.classify(status), "cancelled")

    def test_unknown_status_falls_back_to_other_and_is_excluded(self) -> None:
        """宁可少算并说明，也不要多算不说。"""
        for status in ("莫名其妙的状况", "", "   "):
            with self.subTest(status=repr(status)):
                self.assertEqual(DEFAULT_STATUS_POLICY.classify(status), "other")

    def test_policy_requires_a_consumed_status(self) -> None:
        """否则所有订单都会被排除、合计恒为 0——这种配置必须直接报错。"""
        with self.assertRaises(ValueError) as ctx:
            OrderStatusPolicy.from_dict({"pending": ["待支付"]})
        self.assertIn("consumed", str(ctx.exception))

    def test_policy_from_dict_reads_all_buckets(self) -> None:
        policy = OrderStatusPolicy.from_dict({
            "consumed": ["完成"], "pending": ["等付款"],
            "cancelled": ["作废"], "in_progress": ["做菜中"],
        })
        self.assertEqual(policy.classify("完成"), "consumed")
        self.assertEqual(policy.classify("等付款"), "pending")
        self.assertEqual(policy.classify("作废"), "cancelled")
        self.assertEqual(policy.classify("做菜中"), "in_progress")


class TestOrderCaloriesStatus(unittest.TestCase):
    def test_pending_order_carries_its_status_class(self) -> None:
        result = compute_order(order_with("待支付"), build_matcher())
        self.assertEqual(result.status_class, "pending")
        self.assertFalse(result.is_consumed)

    def test_pending_order_still_computes_calories(self) -> None:
        """待支付订单的卡路里仍要算出来——它本身是有用信息，只是不混进合计。"""
        result = compute_order(order_with("待支付"), build_matcher())
        self.assertGreater(result.known_kcal, 0)
        self.assertTrue(result.is_complete)

    def test_only_consumed_orders_are_flagged_for_totalling(self) -> None:
        orders = [order_with("订单已完成", "A"), order_with("待支付", "B"),
                  order_with("已取消", "C"), order_with("怪状态", "D")]
        results = compute_orders(orders, build_matcher())
        flags = {o.order_id: o.is_consumed for o in results}
        self.assertEqual(flags, {"A": True, "B": False, "C": False, "D": False})

    def test_custom_policy_is_applied(self) -> None:
        policy = OrderStatusPolicy.from_dict({"consumed": ["已完成"], "pending": ["未付"]})
        result = compute_order(order_with("未付"), build_matcher(), policy)
        self.assertEqual(result.status_class, "pending")
        result = compute_order(order_with("已完成"), build_matcher(), policy)
        self.assertTrue(result.is_consumed)


if __name__ == "__main__":
    unittest.main()
