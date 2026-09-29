from lifelog.tags import parse_tags


def test_tags_in_order_of_appearance_without_hash():
    assert parse_tags("#study #math reviewing chapter 3") == ["study", "math"]


def test_tags_are_lowercased():
    assert parse_tags("#Study #DeepWork") == ["study", "deepwork"]


def test_duplicate_tags_keep_first_position():
    assert parse_tags("#math #study #Math") == ["math", "study"]


def test_glued_tags_are_split():
    assert parse_tags("#study#math") == ["study", "math"]


def test_allowed_characters_follow_timetagger():
    assert parse_tags("#study/math #deep-work #leisure_reading") == [
        "study/math",
        "deep-work",
        "leisure_reading",
    ]


def test_non_ascii_characters_are_part_of_the_tag():
    assert parse_tags("#café, then #academia.") == ["café", "academia"]


def test_standalone_hash_and_empty_input_give_no_tags():
    assert parse_tags("# nothing here") == []
    assert parse_tags("") == []
    assert parse_tags(None) == []


def test_hidden_prefix_does_not_affect_tags():
    assert parse_tags("HIDDEN #gym") == ["gym"]
