"""Fixture migrations use the same atomic transfer as production."""


def transfer_project(store, project_id, user_id):
    previous = store.project_access(project_id)['owner']['id']
    if store.role(user_id, project_id) is None:
        store.set_member(project_id, user_id, 'operator')
    return store.transfer_owner(project_id, user_id, actor_id=previous, expected_owner_id=previous)
