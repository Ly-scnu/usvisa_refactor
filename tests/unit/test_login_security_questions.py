from importlib import import_module


login_executor = import_module("05_stage_components.stage03_login.executor")


def test_security_question_fields_not_ready_when_loading_text_only():
    fields = [
        {"question_text": "Loading...", "id": "kba1_response"},
        {"question_text": "", "id": "kba2_response"},
    ]
    assert login_executor._security_fields_ready(fields, 2) is False


def test_security_question_fields_ready_with_two_meaningful_questions():
    fields = [
        {"question_text": "What is your mother's maiden name?", "id": "kba1_response"},
        {"question_text": "What is the middle name of your oldest sibling?", "id": "kba2_response"},
    ]
    assert login_executor._security_fields_ready(fields, 2) is True
