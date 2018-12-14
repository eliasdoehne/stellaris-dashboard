PARSER_TEST_CASES = dict(
    basic_object=dict(
        input="""
                key1=value1
                key2={ list of values }
                key3={ {} {1 2 3} }""",
        expected=dict(
            key1="value1",
            key2=["list", "of", "values"],
            key3=[[], [1, 2, 3]],
        ),
    ),
    multiple_mixed_values_for_same_key=dict(
        input="""
                key=value
                key={}
                key={ innerkey=val } 
                key={ {} {1 2 3} }""",
        expected=dict(
            key=[
                "value",
                [],
                dict(innerkey="val"),
                [[], [1, 2, 3]]
            ]
        ),
    ),
    multiple_mixed_values_list_first=dict(
        input="""
                key={}
                key=value
                key={ innerkey=val } 
                key={ {} {1 2 3} }""",
        expected=dict(
            key=[
                [],
                "value",
                dict(innerkey="val"),
                [[], [1, 2, 3]]
            ]
        ),
    ),
    multiple_list_values_for_same_key=dict(
        input="""
            amount={ 1 2 3 } 
            amount={ 4 5 6 } 
            amount={ 7 8 8 }""",
        expected=dict(
            amount=[
                [1, 2, 3],
                [4, 5, 6],
                [7, 8, 8],
            ]
        ),
    )
)
