import os, pytest
from amlagent.data import generate

@pytest.fixture(scope="session", autouse=True)
def bank(tmp_path_factory):
    d = tmp_path_factory.mktemp("bank")
    os.environ["AML_DB"] = str(d / "bank.db")
    generate(d / "bank.db", d / "labels.json")
    return str(d / "labels.json")
