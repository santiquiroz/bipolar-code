from app.services import telegram_bot


def test_parse_allowed_chat_ids_parses_valid_csv():
    assert telegram_bot.parse_allowed_chat_ids("123,456,-789") == {123, 456, -789}


def test_parse_allowed_chat_ids_tolerates_spaces_and_empty_entries():
    assert telegram_bot.parse_allowed_chat_ids(" 123, , 456 ,") == {123, 456}


def test_parse_allowed_chat_ids_skips_garbage_entries():
    assert telegram_bot.parse_allowed_chat_ids("123,nope,4.5,456") == {123, 456}


def test_parse_allowed_chat_ids_empty_string_returns_empty_set():
    assert telegram_bot.parse_allowed_chat_ids("") == set()


def test_chunk_text_short_text_returns_single_chunk():
    assert telegram_bot.chunk_text("respuesta breve") == ["respuesta breve"]


def test_chunk_text_preserves_whole_lines_when_possible():
    text = "uno\ndos\ntres"

    assert telegram_bot.chunk_text(text, limit=8) == ["uno\ndos\n", "tres"]


def test_chunk_text_hard_splits_a_line_longer_than_limit():
    assert telegram_bot.chunk_text("abcdefghij", limit=4) == ["abcd", "efgh", "ij"]


def test_chunk_text_empty_text_returns_empty_list():
    assert telegram_bot.chunk_text("") == []


def test_build_chat_body_uses_default_model():
    body = telegram_bot.build_chat_body([], "hola")

    assert body["model"] == "claude-sonnet-4-6"


def test_build_chat_body_prepends_history_before_new_user_message():
    history = [
        {"role": "user", "content": "pregunta anterior"},
        {"role": "assistant", "content": "respuesta anterior"},
    ]

    body = telegram_bot.build_chat_body(history, "pregunta nueva")

    assert body["messages"] == history + [
        {"role": "user", "content": "pregunta nueva"}
    ]


def test_build_chat_body_disables_streaming():
    body = telegram_bot.build_chat_body([], "hola")

    assert body["stream"] is False


def test_trim_history_under_limit_returns_history_untouched():
    history = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "hola"},
    ]

    result = telegram_bot.trim_history(history, max_turns=2)

    assert result is history


def test_trim_history_over_limit_keeps_most_recent_turns():
    history = [{"role": "user", "content": str(index)} for index in range(8)]

    result = telegram_bot.trim_history(history, max_turns=2)

    assert result == history[-4:]
