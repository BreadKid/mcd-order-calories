"""测试用的合成数据。

刻意不联网：归一逻辑的正确性不应该依赖真实账号数据。
但表里的商品名都取自**实测真实数据**（包括那两个假阳性案例），
所以这些测试是回归防线，不是摆设。
"""

from __future__ import annotations

# 模拟 list-nutrition-foods 的返回文本（含「字段说明 + JSON」的包装）
NUTRITION_FIELDS = "productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium"

NUTRITION_ROWS = [
    "可乐中杯,null,616,147,0,0,37,10,0",
    "无糖可口可乐中杯,null,4,1,0,0,0,10,0",
    "可乐大杯,null,924,221,0,0,55,15,0",
    "中薯条,null,1210,289,4,12,38,165,18",
    "大薯条,null,1587,379,6,16,50,216,23",
    "双层吉士汉堡,null,1795,429,25,22,33,1100,230",
    "那么大鸡排,null,1611,385,22,22,24,900,20",
    "双层深海鳕鱼堡,null,2029,485,22,22,51,1090,180",
    "麦辣鸡翅-2块,null,937,224,13,15,9,537,13",
    "猪柳麦满分,null,1288,308,16,16,24,781,213",
    "纯牛奶（盒装）,null,540,129,6,7,10,100,200",
    "冰燕麦奶铁中杯,null,552,132,4,5,18,90,150",
    "热燕麦奶铁中杯,null,540,129,4,5,18,90,150",
    "【美汁源】“黄金橙橙”,null,690,165,0,0,41,20,0",
    "小杯玉米杯,null,223,53,2,1,7,1,6",
    "小杯玉米杯,null,223,53,2,1,7,1,6",
]

# 表头声明 160 条，实际只有 15 行 → 用于验证解析器会如实报警
NUTRITION_DECLARED = 160


def nutrition_payload(declared: int = NUTRITION_DECLARED) -> str:
    lines = [f"[{declared}]{{{NUTRITION_FIELDS}}}:"]
    lines += [f"  {row}" for row in NUTRITION_ROWS]
    return "\n".join(lines)


def wrapped(payload_json: str) -> str:
    """模拟真实返回：字段说明段 + Original Response 段。

    说明段里刻意保留 `data[].storeName` 这种带方括号的写法——
    简单正则会在这里翻车，这正是回归点。
    """
    return (
        "# API Response Information\n\n"
        "Below is the response from an API call.\n\n"
        "## Response Structure\n\n"
        "- **门店名称**: data[].storeName\n"
        "- **门店编号**: data[].storeCode\n\n"
        "## Original Response\n\n"
        f"{payload_json}"
    )


def order(order_id: str, create_time: str, paid: str, products: list[dict]) -> dict:
    return {
        "orderId": order_id,
        "orderType": "1",
        "createTime": create_time,
        "beType": "1",
        "beCode": "",
        "storeCode": "1450104",
        "storeName": "麦当劳上海黄浦悦荟广场餐厅",
        "orderStatus": "订单已完成",
        "orderProductList": products,
        "realTotalAmount": paid,
    }


def combo(product_code: str, name: str, items: list[tuple[str, str, int]], quantity: int = 1) -> dict:
    return {
        "productCode": product_code,
        "productName": name,
        "quantity": quantity,
        "comboItemList": [
            {"productCode": code, "name": item_name, "quantity": qty}
            for code, item_name, qty in items
        ],
    }


def single(product_code: str, name: str, quantity: int = 1) -> dict:
    return {"productCode": product_code, "productName": name, "quantity": quantity}


# 覆盖各种情况的一组订单
ORDERS = [
    # 完全覆盖：套餐展开成两个有营养数据的成分
    order("O1", "2026-09-18 12:29:06", "14.9",
          [combo("9900013304", "人气经典随心配", [("1120", "双层吉士汉堡", 1), ("4437", "小杯玉米杯", 1)])]),
    # 部分覆盖：含营养表未收录的限定品
    order("O2", "2026-09-07 18:10:14", "62.9",
          [single("111", "世界波脆鸡排蛋堡"), single("222", "中薯条")]),
    # 蘸酱：不计入覆盖率分母
    order("O3", "2026-08-19 12:37:00", "38.4",
          [single("333", "双层吉士汉堡"), single("444", "日式鲜磨山葵酱")]),
    # 不是蘸酱的「蘸酱xx」：必须计入分母并如实标为缺口
    order("O4", "2026-08-01 12:00:00", "40.0",
          [single("555", "蘸酱鸡球（甜酸酱风味）")]),
]


# 测试用品类规则：蘸酱（不计入覆盖率）+ 【美汁源】（计入覆盖率）
CATEGORY_RULES = {
    "rules": [
        {
            "id": "sauce",
            "label": "蘸酱",
            "match": {"type": "suffix", "value": "酱"},
            "default_kcal": 0,
            "exclude_from_coverage": True,
        },
        {
            "id": "minute_maid",
            "label": "【美汁源】饮料",
            "match": {"type": "prefix", "value": "【美汁源】"},
            "default_kcal": 165,
            "exclude_from_coverage": False,
        },
    ]
}


def category_catalog(default_sauce_kcal: float = 0.0):
    import copy
    from mcd_order_calories.categories import CategoryCatalog

    data = copy.deepcopy(CATEGORY_RULES)
    data["rules"][0]["default_kcal"] = default_sauce_kcal
    return CategoryCatalog.from_dict(data)


# 测试用补充热量表
SUPPLEMENT_RULES = {
    "supplements": {
        "双层脆鸡堡": {
            "kcal": 520,
            "range": [490, 560],
            "confidence": "medium",
            "derivation": "面包113 + 2×鸡排152 + 酱100 = 517；比例法交叉验证 538。取中值 520。",
            "source": "由营养表内成对条目反推，非官方数据",
        }
    }
}


def supplement_catalog():
    from mcd_order_calories.supplements import SupplementCatalog

    return SupplementCatalog.from_dict(SUPPLEMENT_RULES)
