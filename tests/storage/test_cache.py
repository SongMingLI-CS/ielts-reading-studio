from app.storage.cache import stage_cache_key


def test_cache_key_changes_only_when_semantic_inputs_change():
    first = stage_cache_key("author", "source", "standard", ["matching_headings"], "deepseek-flash", "p1", {"temperature": 0.3})
    same = stage_cache_key("author", "source", "standard", ["matching_headings"], "deepseek-flash", "p1", {"temperature": 0.3})
    changed = stage_cache_key("author", "source", "advanced", ["matching_headings"], "deepseek-flash", "p1", {"temperature": 0.3})

    assert first == same
    assert first != changed


def test_cache_key_canonicalizes_parameter_dictionary_order():
    first = stage_cache_key("author", "source", "standard", ["matching_headings"], "deepseek-flash", "p1", {"temperature": 0.3, "top_p": 0.9})
    reordered = stage_cache_key("author", "source", "standard", ["matching_headings"], "deepseek-flash", "p1", {"top_p": 0.9, "temperature": 0.3})

    assert first == reordered
