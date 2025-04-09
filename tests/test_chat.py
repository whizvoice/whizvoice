def test_execute_tool():
    result = execute_tool('get_asana_workspaces', {})
    assert isinstance(result, list)

def test_chat_session():
    mock_client = MagicMock()
    session = ChatSession(mock_client)
    response = session.handle_message("show my workspaces")
    assert response.content[0].type == 'text' 