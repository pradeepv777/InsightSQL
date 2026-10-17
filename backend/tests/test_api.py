import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_api_health():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["healthy", "degraded"]

def test_create_conversation_and_chat():
    # 1. Create conversation
    conv_res = client.post("/api/v1/conversations")
    assert conv_res.status_code == 200
    conv_id = conv_res.json()["id"]
    
    # 2. List conversations
    conv_list = client.get("/api/v1/conversations")
    assert conv_list.status_code == 200
    assert any(c["id"] == conv_id for c in conv_list.json())

    # 3. Upload CSV
    csv_content = b"item,amount\nA,10\nB,20\n"
    up_res = client.post(
        "/api/v1/datasets",
        files={"file": ("test_sales.csv", csv_content, "text/csv")}
    )
    assert up_res.status_code == 200
    ds_id = up_res.json()["id"]
    
    # 4. List datasets
    ds_list = client.get("/api/v1/datasets")
    assert ds_list.status_code == 200
    assert any(d["id"] == ds_id for d in ds_list.json())

    # 5. Get dataset metadata
    ds_meta = client.get(f"/api/v1/datasets/{ds_id}")
    assert ds_meta.status_code == 200
    assert ds_meta.json()["id"] == ds_id

    # 6. Preview dataset
    prev_res = client.get(f"/api/v1/datasets/{ds_id}/preview")
    assert prev_res.status_code == 200
    assert "columns" in prev_res.json()
    assert len(prev_res.json()["rows"]) == 2

    # 7. Profile dataset
    prof_res = client.get(f"/api/v1/datasets/{ds_id}/profile")
    assert prof_res.status_code == 200
    assert len(prof_res.json()["columns"]) == 2

    # 8. Post analytical message
    msg_res = client.post(
        f"/api/v1/conversations/{conv_id}/messages",
        json={"message": "How many rows and columns does this dataset have?", "dataset_ids": [ds_id]}
    )
    assert msg_res.status_code == 200
    msg_data = msg_res.json()
    assert msg_data["status"] == "success"
    assert "2 rows" in msg_data["answer"]
    
    # 9. Check conversation history
    hist_res = client.get(f"/api/v1/conversations/{conv_id}/messages")
    assert hist_res.status_code == 200
    assert len(hist_res.json()) >= 2

    # 10. Delete conversation
    del_res = client.delete(f"/api/v1/conversations/{conv_id}")
    assert del_res.status_code == 200

    # 11. Delete dataset
    del_ds = client.delete(f"/api/v1/datasets/{ds_id}")
    assert del_ds.status_code == 200

def test_api_error_cases():
    # Invalid dataset
    bad_ds = client.get("/api/v1/datasets/non-existent-id")
    assert bad_ds.status_code == 404

    # Invalid conversation
    bad_conv = client.delete("/api/v1/conversations/non-existent-id")
    assert bad_conv.status_code == 404

    # Empty message
    conv_res = client.post("/api/v1/conversations")
    c_id = conv_res.json()["id"]
    empty_msg = client.post(f"/api/v1/conversations/{c_id}/messages", json={"message": "   "})
    assert empty_msg.status_code == 400
    client.delete(f"/api/v1/conversations/{c_id}")

