"""Live updates must follow committed writes without repeated dashboard polling."""
import asyncio
import httpx
from fastapi.testclient import TestClient
from backend.app import create_app

AUTH={'X-Device-Token':'test-secret'}


def test_changes_auth_validation_timeout_and_restart(tmp_path):
    path=tmp_path/'changes.db'
    with TestClient(create_app(path,'test-secret')) as client:
        assert client.get('/api/v1/changes?wait_ms=0').status_code==401
        initial=client.get('/api/v1/changes?wait_ms=0',headers=AUTH).json()
        assert initial['changed'] is True
        assert client.get('/api/v1/changes',params={'after':initial['revision'],'wait_ms':1},headers=AUTH).json()['changed'] is False
        assert client.get('/api/v1/changes?wait_ms=20001',headers=AUTH).status_code==422
        assert client.post('/api/v1/heartbeat',json={'device_id':'unit'},headers={'X-Device-Token':'wrong'}).status_code==401
        assert client.get('/api/v1/changes?wait_ms=0',headers=AUTH).json()['revision']==initial['revision']
    with TestClient(create_app(path,'test-secret')) as client:
        assert client.get('/api/v1/changes',params={'after':initial['revision'],'wait_ms':0},headers=AUTH).json()['changed'] is True


def test_held_change_request_wakes_for_committed_trip_and_multiple_listeners(tmp_path):
    async def scenario():
        app=create_app(tmp_path/'wake.db','test-secret')
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers=AUTH) as client:
                first=(await client.get('/api/v1/changes?wait_ms=0')).json()['revision']
                listeners=[asyncio.create_task(client.get('/api/v1/changes',params={'after':first})) for _ in range(2)]
                await asyncio.sleep(.02)
                assert not any(task.done() for task in listeners)
                trip=await client.post('/api/v1/trips',json={'name':'Wake trip','device_id':'unit','origin':'A','destination':'B'})
                assert trip.status_code==201
                results=await asyncio.wait_for(asyncio.gather(*listeners),2)
                assert all(r.json()['changed'] for r in results)
                assert all(r.json()['revision']!=first for r in results)
                detail=await client.get('/api/v1/trips/'+trip.json()['id'])
                assert detail.status_code==200
    asyncio.run(scenario())
