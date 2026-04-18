import json
from typing import Any


def prisma_json(value: Any) -> str:
    return json.dumps(value, default=str)
