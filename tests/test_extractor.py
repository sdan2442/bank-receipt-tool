from decimal import Decimal

from bank_receipt_tool.domain import OCRLine
from bank_receipt_tool.extractor import FieldExtractor, chinese_amount, normalize_amount, normalize_date


def line(text, x=0, y=0, confidence=.92):
    return OCRLine(text, [[x,y],[x+220,y],[x+220,y+30],[x,y+30]], confidence)


def box_line(text, x, y, width, height, confidence=.98):
    return OCRLine(text, [[x, y], [x + width, y], [x + width, y + height], [x, y + height]], confidence)


RULES = {"common": {
    "date_keywords": ["交易日期"], "payer_keywords": ["付款人", "付款人名称"],
    "payee_keywords": ["收款人", "收款人名称"], "amount_keywords": ["金额", "交易金额"],
    "amount_exclude_keywords": ["余额", "手续费", "账号"],
}}


def test_date_formats_and_invalid_date():
    assert normalize_date("2026-09-08 12:30:01") == "2026-09-08"
    assert normalize_date("2026/9/8") == "2026-09-08"
    assert normalize_date("2026.09.08") == "2026-09-08"
    assert normalize_date("20260908") == "2026-09-08"
    assert normalize_date("2026年09月08日") == "2026-09-08"
    assert normalize_date("2026-02-31") is None


def test_amount_formats():
    assert normalize_amount("￥12,500.00") == "12500.00"
    assert normalize_amount("¥ 12500.5") == "12500.50"
    assert chinese_amount("人民币壹万贰仟伍佰元整") == "12500.00"
    assert chinese_amount("贰拾肆万伍仟陆佰壹拾肆元肆角伍分") == "245614.45"


def test_uppercase_amount_can_be_combined_across_ocr_lines():
    lines = [
        line("票据金额", 10, 100, .98),
        line("人民币壹万贰仟", 250, 100, .96),
        line("伍佰元整", 470, 100, .97),
    ]
    fields = FieldExtractor(RULES).extract(lines)
    assert fields.amount == "12500.00"
    assert fields.amount_candidates[0].source == "跨行中文大写金额"


def test_invalid_mobile_uppercase_amount_is_reviewed_without_crashing(monkeypatch):
    real_cn2an = __import__("bank_receipt_tool.extractor", fromlist=["cn2an"]).cn2an.cn2an

    def fail_only_malformed(value, mode):
        if value == "壹万贰拾":
            raise KeyError(1)
        return real_cn2an(value, mode)

    monkeypatch.setattr("bank_receipt_tool.extractor.cn2an.cn2an", fail_only_malformed)
    lines = [
        line("交易金额 12,500.00", 10, 100, .99),
        line("金额（大写）", 500, 100, .90),
        line("人民币壹万贰拾元整", 800, 100, .72),
    ]
    fields = FieldExtractor(RULES).extract(lines)
    assert fields.amount == "12500.00"
    assert "中文大写金额格式异常" in fields.review_reasons


def test_chinese_amount_key_error_returns_no_candidate(monkeypatch):
    monkeypatch.setattr(
        "bank_receipt_tool.extractor.cn2an.cn2an",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyError(1)),
    )
    assert chinese_amount("人民币壹万贰拾元整") is None


def test_grid_amount_is_rebuilt_by_coordinates_and_cells_are_not_candidates():
    def cell(text, x, y, confidence=.98):
        return OCRLine(text, [[x, y], [x + 28, y], [x + 28, y + 28], [x, y + 28]], confidence)

    lines = [cell("票据金额", 10, 100), cell("元", 500, 80), cell("角", 550, 80), cell("分", 600, 80)]
    for index, digit in enumerate("24561445"):
        lines.append(cell(digit, 250 + index * 50, 130, .99))
    lines.append(cell("2025-04-22", 250, 170))

    fields = FieldExtractor(RULES).extract(lines)
    assert fields.amount == "245614.45"
    assert fields.amount_candidates[0].source == "金额表格坐标重组"
    assert all(candidate.value not in {"2.00", "4.00", "5.00"} for candidate in fields.amount_candidates)


def test_keyword_coordinates_and_payee_exclusion():
    lines = [line("交易日期", 10, 10), line("2026-09-08", 300, 10),
             line("付款人名称：上海示例贸易有限公司", 10, 70),
             line("收款人名称：北京示例公司", 10, 120),
             line("交易金额", 10, 170), line("￥12,500.00", 300, 170)]
    fields = FieldExtractor(RULES).extract(lines)
    assert fields.date == "2026-09-08"
    assert fields.payer == "上海示例贸易有限公司"
    assert fields.payee == "北京示例公司"
    assert fields.amount == "12500.00"
    assert "北京示例公司" not in fields.payer


def test_multiple_amounts_require_review_and_fees_excluded():
    lines = [line("交易金额 12,500.00", 10, 10), line("金额 13,000.00", 10, 60), line("手续费 5.00", 10, 110)]
    fields = FieldExtractor(RULES).extract(lines)
    assert "找到多个金额候选" in fields.review_reasons
    assert all(c.value != "5.00" for c in fields.amount_candidates)


def test_table_payer_is_bound_to_payer_name_cell_not_neighbor_labels_or_payee():
    lines = [
        box_line("付款人", 100, 220, 45, 145),
        box_line("户名", 240, 240, 80, 50),
        box_line("上海示例科技有限公司", 410, 240, 390, 50),
        box_line("收款", 900, 240, 50, 100),
        box_line("户名", 1040, 240, 80, 50),
        box_line("北京示例贸易有限公司", 1200, 240, 390, 50),
        box_line("账号", 240, 325, 80, 50),
        box_line("1234000000005678", 410, 325, 300, 45),
        box_line("人", 900, 340, 45, 55),
        box_line("账号", 1040, 325, 80, 50),
        box_line("交易币种", 200, 400, 140, 50),
        box_line("人民币", 410, 400, 100, 50),
    ]
    fields = FieldExtractor(RULES).extract(lines)
    assert fields.payer == "上海示例科技有限公司"
    assert fields.payee == "北京示例贸易有限公司"
    assert [candidate.value for candidate in fields.payer_candidates] == ["上海示例科技有限公司"]
    assert "找到多个付款人候选" not in fields.review_reasons


def test_wrapped_payer_name_is_reconstructed_inside_its_table_cell():
    lines = [
        box_line("付款人", 100, 220, 45, 145),
        box_line("户名", 240, 240, 80, 50),
        box_line("北京示例集成电路创新中心有限公", 410, 215, 390, 35),
        box_line("司", 590, 270, 35, 35),
        box_line("收款人", 900, 220, 45, 145),
        box_line("户名", 1040, 240, 80, 50),
        box_line("合肥示例半导体设备科技有限公司", 1200, 240, 390, 50),
        box_line("账号", 240, 325, 80, 50),
        box_line("631278953", 410, 325, 220, 45),
        box_line("账号", 1040, 325, 80, 50),
    ]
    fields = FieldExtractor(RULES).extract(lines)
    assert fields.payer == "北京示例集成电路创新中心有限公司"
    assert fields.payer_candidates[0].source.endswith("(跨行户名)")
    assert "付款人名称疑似截断" not in fields.review_reasons


def test_unresolved_truncated_payer_name_requires_review():
    lines = [
        box_line("付款人", 100, 220, 45, 145),
        box_line("户名", 240, 240, 80, 50),
        box_line("北京示例集成电路创新中心有限公", 410, 240, 390, 40),
        box_line("账号", 240, 325, 80, 50),
    ]
    fields = FieldExtractor(RULES).extract(lines)
    assert fields.payer == "北京示例集成电路创新中心有限公"
    assert "付款人名称疑似截断" in fields.review_reasons


def test_mobile_split_payer_label_is_reconstructed_before_cell_lookup():
    lines = [
        box_line("付款", 100, 220, 45, 60),
        box_line("户名", 240, 230, 80, 50),
        box_line("合肥示例显示科技有限公司", 410, 230, 390, 45),
        box_line("收款", 900, 220, 45, 60),
        box_line("户名", 1040, 230, 80, 50),
        box_line("合肥示例半导体设备科技有限公司", 1200, 230, 390, 45),
        box_line("人", 100, 290, 45, 40),
        box_line("账号", 240, 325, 80, 50),
        box_line("人", 900, 290, 45, 40),
        box_line("账号", 1040, 325, 80, 50),
    ]
    fields = FieldExtractor(RULES).extract(lines)
    assert fields.payer == "合肥示例显示科技有限公司"
    assert fields.payee == "合肥示例半导体设备科技有限公司"
    assert "付款人缺失" not in fields.review_reasons


def test_credit_advice_uses_counterparty_as_payer_and_customer_as_payee():
    rules = {"common": {
        "primary_date_keywords": ["交易时间"],
        "date_keywords": ["交易日"],
        "payer_keywords": ["付款人"],
        "payee_keywords": ["收款人"],
        "amount_keywords": ["金额"],
        "amount_exclude_keywords": ["余额", "手续费", "账号"],
    }}
    lines = [
        box_line("贷记通知(Credit Advice)", 200, 20, 330, 36),
        box_line("CUSTOMER NAME/客户名称", 80, 100, 300, 34),
        box_line("浙江示例设备有限公司", 80, 145, 300, 34),
        box_line("交易日", 80, 240, 100, 34),
        box_line("金额", 330, 240, 100, 34),
        box_line("COUNTERPARTY NAME/交易对方名字", 600, 240, 350, 34),
        box_line("20250410", 80, 290, 150, 34),
        box_line("2,775,901.50", 330, 290, 180, 34),
        box_line("长鑫示例技术有限公司", 600, 290, 300, 34),
    ]

    fields = FieldExtractor(rules).extract(lines)

    assert fields.date == "2025-04-10"
    assert fields.payer == "长鑫示例技术有限公司"
    assert fields.payee == "浙江示例设备有限公司"
    assert fields.amount == "2775901.50"
    assert not any(reason.endswith("缺失") for reason in fields.review_reasons)


def test_traditional_household_label_never_joins_payer_and_payee_columns():
    """Regression for ICBC OCR returning 戶名 in the right-hand column."""
    lines = [
        box_line("武汉示例科技有限公司", 472, 287, 271, 34),
        box_line("上海示例电子科技有限公司", 1138, 288, 342, 33),
        box_line("户名", 345, 304, 89, 37),
        box_line("戶名", 1004, 302, 90, 42),
        box_line("付款", 247, 360, 63, 40),
        box_line("收款", 905, 362, 63, 35),
        box_line("账号", 346, 379, 89, 37),
        box_line("账号", 1005, 379, 89, 36),
        box_line("人", 258, 396, 37, 37),
        box_line("人", 918, 397, 35, 33),
    ]
    fields = FieldExtractor(RULES).extract(lines)
    assert fields.payer == "武汉示例科技有限公司"
    assert fields.payee == "上海示例电子科技有限公司"
    assert "户名" not in fields.payer
    assert "戶名" not in fields.payer
    assert fields.payer != fields.payee


def test_payee_footer_sentences_are_not_party_candidates():
    fields = FieldExtractor(RULES).extract([
        line("1.如果您是收款方，请到银行网站验证。", 10, 100),
        line("2.本回单不作为收款方发货依据。", 10, 150),
    ])
    assert fields.payee is None
    assert fields.payee_candidates == []


def test_transaction_uppercase_amount_excludes_separate_fee_uppercase_row():
    lines = [
        box_line("金额（小写）", 200, 470, 190, 50),
        box_line("357,080.00", 520, 470, 170, 45),
        box_line("金额", 900, 470, 80, 50),
        box_line("大写", 1020, 470, 90, 50),
        box_line("叁拾伍万柒仟零捌拾元整", 1200, 470, 360, 45),
        box_line("手续费（小写）", 200, 525, 200, 50),
        box_line("0.00", 520, 525, 80, 45),
        box_line("手续费大写", 900, 525, 230, 50),
        box_line("零元整", 1200, 525, 110, 45),
    ]
    fields = FieldExtractor(RULES).extract(lines)
    assert fields.amount == "357080.00"
    assert [candidate.value for candidate in fields.amount_candidates] == ["357080.00"]
    assert "找到多个金额候选" not in fields.review_reasons


def test_missing_date_requires_review():
    fields = FieldExtractor(RULES).extract([line("付款人：示例公司"), line("金额 100.00", y=50)])
    assert "日期缺失" in fields.review_reasons


def test_low_confidence_requires_review():
    fields = FieldExtractor(RULES, .8).extract([line("交易日期 2026-09-08", confidence=.4)])
    assert "日期置信度低" in fields.review_reasons


def test_bank_branch_as_payer_requires_review():
    fields = FieldExtractor(RULES).extract([
        line("交易日期 2026-09-08", 10, 10, .99),
        line("付款人名称：中国建设银行股份有限公司上海浦东支行", 10, 60, .99),
        line("收款人名称：上海示例贸易有限公司", 10, 110, .99),
        line("交易金额 12,500.00", 10, 160, .99),
    ])
    assert fields.payer == "中国建设银行股份有限公司上海浦东支行"
    assert any("付款人疑似银行机构" in reason and "支行" in reason for reason in fields.review_reasons)


def test_company_with_bank_in_business_name_is_not_false_positive():
    fields = FieldExtractor(RULES).extract([
        line("付款人名称：上海银行设备科技有限公司", 10, 60, .99),
    ])
    assert fields.payer == "上海银行设备科技有限公司"
    assert not any("付款人疑似银行机构" in reason for reason in fields.review_reasons)


def test_institution_labels_never_become_party_names_and_row_fallback_recovers_names():
    """Regression for a low-resolution 业务回单（收款） mobile OCR page."""
    lines = [
        box_line("业务回单", 1250, 240, 220, 42, .99),
        box_line("（收款）", 1490, 245, 100, 36, .97),
        box_line("深圳示例技术有限公司", 250, 340, 430, 36, .99),
        box_line("付款人开户行：中国建设银行股份有限公司深圳中心区支", 950, 340, 760, 38, .99),
        box_line("账号（卡号）：44250100000000000000-3001", 250, 385, 520, 36, .98),
        box_line("行", 1000, 385, 30, 34, .99),
        box_line("人户名：上海示例电子科技有限公司", 250, 430, 480, 36, .97),
        box_line("收款人开户行：浦东东方路支行", 950, 430, 470, 38, .99),
        box_line("收款人账号（卡号）：1001128209000000000", 250, 475, 520, 36, .99),
        box_line("小写：19,102,650.00元", 1050, 495, 340, 38, .95),
    ]

    fields = FieldExtractor(RULES).extract(lines)

    assert fields.payer == "深圳示例技术有限公司"
    assert fields.payee == "上海示例电子科技有限公司"
    assert fields.payer_confidence <= .82
    assert fields.payee_confidence <= .82
    assert fields.payer_candidates[0].source == "开户行同排户名回退"
    assert fields.payee_candidates[0].source == "开户行同排户名回退"
    assert all("开户行" not in candidate.value for candidate in fields.payer_candidates)
    assert all("开户行" not in candidate.value for candidate in fields.payee_candidates)


def test_institution_labels_without_a_name_do_not_create_false_party_candidates():
    fields = FieldExtractor(RULES).extract([
        box_line("付款人开户行：中国建设银行股份有限公司深圳中心区支", 950, 340, 760, 38, .99),
        box_line("收款人开户行：浦东东方路支行", 950, 430, 470, 38, .99),
    ])
    assert fields.payer is None
    assert fields.payee is None
    assert "付款人缺失" in fields.review_reasons


def test_party_account_label_is_not_used_as_a_party_anchor():
    fields = FieldExtractor(RULES).extract([
        box_line("人户名：上海示例电子科技有限公司", 300, 430, 450, 36, .98),
        box_line("收款人账号（卡号）：1001128209000000000", 120, 475, 650, 36, .99),
    ])
    assert fields.payee is None
    assert fields.payee_candidates == []


def test_bank_branch_as_payee_requires_review_too():
    fields = FieldExtractor(RULES).extract([
        line("付款人名称：上海示例贸易有限公司", 10, 60, .99),
        line("收款人名称：中国银行上海浦东支行", 10, 110, .99),
    ])
    assert fields.payee == "中国银行上海浦东支行"
    assert any("收款人疑似银行机构" in reason for reason in fields.review_reasons)



def test_transaction_time_compact_value_is_primary_even_when_above_keyword():
    rules = {"common": dict(RULES["common"], primary_date_keywords=["交易时间"],
                            date_keywords=["交易时间", "记账日期"])}
    lines = [
        line("记账日期 2025-12-18", 10, 10, .99),
        line("交易时间", 10, 200, .95),
        # OCR reading order follows the keyword, while inverted image coordinates
        # place the value physically above it.
        line("2025-12-1710:21:45", 10, 100, .91),
    ]
    fields = FieldExtractor(rules).extract(lines)
    assert normalize_date("2025-12-1710:21:45") == "2025-12-17"
    assert fields.date == "2025-12-17"
    assert fields.date_candidates[0].source.startswith("关键词:交易时间")
