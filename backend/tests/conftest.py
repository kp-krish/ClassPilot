import json
import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-2")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def asu_classes():
    return json.loads((FIXTURES / "cse230_2271.json").read_text())["classes"]


def _table(db, name, pk, sk):
    db.create_table(
        TableName=name, BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": pk, "AttributeType": "S"},
                              {"AttributeName": sk, "AttributeType": "S"}],
        KeySchema=[{"AttributeName": pk, "KeyType": "HASH"},
                   {"AttributeName": sk, "KeyType": "RANGE"}])


@pytest.fixture
def aws(monkeypatch, asu_classes):
    """Moto DynamoDB plus a fake ASU that serves the CSE 230 fixture."""
    with mock_aws():
        db = boto3.client("dynamodb")
        _table(db, "classpilot-watches", "watcher_id", "class_nbr")
        _table(db, "classpilot-sections", "term", "class_nbr")
        _table(db, "classpilot-alerts", "watcher_id", "seen_at")
        for mod in ("store", "api", "poller"):
            sys.modules.pop(mod, None)
        import asu_seats

        state = {"classes": asu_classes, "reserved": 22, "calls": 0}

        def fake_search(term, **extra):
            state["calls"] += 1
            rows = state["classes"]
            if "keywords" in extra:
                return [c for c in rows if c["CLAS"]["CLASSNBR"] == extra["keywords"]]
            return rows

        monkeypatch.setattr(asu_seats, "_search", fake_search)
        monkeypatch.setattr(asu_seats, "reserved_open", lambda s: state["reserved"])
        yield state
