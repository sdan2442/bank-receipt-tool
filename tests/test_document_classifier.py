from bank_receipt_tool.document_classifier import classify_document
from bank_receipt_tool.config import AppConfig
from bank_receipt_tool.domain import OCRLine


def line(text: str, y: int = 0) -> OCRLine:
    return OCRLine(text, [[0, y], [300, y], [300, y + 30], [0, y + 30]], .98)


RULES = AppConfig.default().load_rules()


def test_classifies_ordinary_bank_receipt():
    result = classify_document([
        line("电子回单"), line("付款人"), line("交易时间"), line("金额（小写）"),
    ], RULES)
    assert result.document_type == "receipt"
    assert result.confidence >= .8


def test_classifies_business_receipt_title_without_other_strong_markers():
    result = classify_document([
        line("业务回单"), line("（收款）"), line("付款人开户行"), line("收款人开户行"),
    ], RULES)
    assert result.document_type == "receipt"
    assert result.confidence >= .8
    assert "业务回单" in result.evidence


def test_classifies_acceptance_bill_front_and_detail_page():
    result = classify_document([
        line("电子银行承兑汇票"), line("出票人信息"), line("承兑人信息"),
        line("票据行为人详细信息"), line("增信信息"),
    ], RULES)
    assert result.document_type == "acceptance_bill"
    assert "电子银行承兑汇票" in result.evidence


def test_classifies_acceptance_bill_endorsement_page_before_front_page():
    result = classify_document([
        line("电子银行承兑汇票"), line("背书人信息"), line("被背书人"), line("背书日期"),
    ], RULES)
    assert result.document_type == "acceptance_bill_endorsement"
    assert result.confidence >= .8


def test_unknown_document_remains_unknown():
    result = classify_document([line("任意扫描资料")], RULES)
    assert result.document_type == "unknown"
    assert result.confidence < .6
