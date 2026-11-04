from web.access_control import _capability


def test_private_chat_catalog_requires_operation_role_for_reads_and_writes():
    for method in ('GET', 'PUT', 'DELETE'):
        resource = 'chat/sessions/item' if method == 'DELETE' else 'chat/sessions'
        assert _capability(method, resource, 'resource') == 'operate'
