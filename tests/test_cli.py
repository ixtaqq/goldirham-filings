import json
from pathlib import PureWindowsPath

from goldirham_filings.cli import mcp_config


def test_mcp_config_is_valid_json_with_escaped_windows_paths() -> None:
    command = PureWindowsPath(
        r"E:\Work Space\goldirham-filings\.venv\Scripts\goldirham-filings-mcp.exe"
    )
    text = json.dumps(mcp_config(command))
    assert "\\\\Work Space\\\\" in text
    assert json.loads(text)["mcpServers"]["goldirham-filings"]["command"] == str(command)
