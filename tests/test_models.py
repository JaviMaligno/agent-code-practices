from acp.models import RepoProfile, SizeMetrics, ReadabilityMetrics


def test_profile_serialises_to_flat_dict():
    profile = RepoProfile(
        name="demo",
        size=SizeMetrics(python_files=3, code_lines=120, max_depth=2, mean_depth=1.5),
        readability=ReadabilityMetrics(
            comment_ratio=0.1,
            docstring_ratio=0.2,
            annotated_function_ratio=0.5,
            has_readme=True,
            has_docs_dir=False,
        ),
    )
    flat = profile.to_flat_dict()
    assert flat["name"] == "demo"
    assert flat["size.code_lines"] == 120
    assert flat["readability.has_readme"] is True
