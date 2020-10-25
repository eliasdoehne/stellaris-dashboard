PARSER_TEST_CASES = dict(
    basic_object=dict(
        input="""
                key1=value1
                key2={ list of values }
                key3={ {} {1 2 3} }""",
        expected=dict(
            key1="value1", key2=["list", "of", "values"], key3=[[], [1, 2, 3]],
        ),
    ),
    multiple_mixed_values_for_same_key=dict(
        input="""
                key_object=value
                key_object={}
                key_object={ innerkey=layout_dict } 
                key_object={ {} {1 2 3} }""",
        expected=dict(
            key_object=["value", [], dict(innerkey="layout_dict"), [[], [1, 2, 3]]]
        ),
    ),
    multiple_mixed_values_list_first=dict(
        input="""
                key_object={}
                key_object=value
                key_object={ innerkey=layout_dict } 
                key_object={ {} {1 2 3} }""",
        expected=dict(
            key_object=[[], "value", dict(innerkey="layout_dict"), [[], [1, 2, 3]]]
        ),
    ),
    multiple_list_values_for_same_key=dict(
        input="""
            amount={ 1 2 3 } 
            amount={ 4 5 6 } 
            amount={ 7 8 8 }""",
        expected=dict(amount=[[1, 2, 3], [4, 5, 6], [7, 8, 8],]),
    ),
    ancient_relic_missing_event_ids=dict(
        input="""expired=yes
        event_id=					scope={
        type=none
        id=0
        random={ 0 3991148998 }
        }""",
        expected=dict(
            expired="yes",
            event_id="scope",
            unknown_key={"type": "none", "id": 0, "random": [0, 3991148998]},
        ),
    ),
)
