from fastapi.testclient import TestClient

from conftest import offside
from vargento import auth
from vargento.importer import import_csv
from vargento.media import embed, start_from_url, youtube_id

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_youtube_and_start_parsing():
    assert youtube_id("https://www.youtube.com/watch?v=abcdefghijk&t=90") == "abcdefghijk"
    assert youtube_id("https://youtu.be/abcdefghijk?t=1m5s") == "abcdefghijk"
    assert youtube_id("https://youtube.com/shorts/abcdefghijk") == "abcdefghijk"
    assert youtube_id("https://example.com/watch?v=abcdefghijk") is None
    assert start_from_url("https://youtu.be/abcdefghijk?t=1m5s") == 65
    assert start_from_url("https://youtu.be/abcdefghijk?t=90") == 90
    yt = embed({"kind": "link", "url": "https://youtu.be/abcdefghijk", "start_seconds": 12})
    assert yt == {"type": "iframe", "src": "https://www.youtube-nocookie.com/embed/abcdefghijk?start=12&rel=0"}
    assert embed({"kind": "link", "url": "https://x.com/a.mp4", "start_seconds": 0})["type"] == "video"
    assert embed({"kind": "link", "url": "https://x.com/nota", "start_seconds": 0})["type"] == "link"


def test_attach_media_to_review_and_it_moves_to_history(login):
    client = login("periodista")
    ref_id = client.get("/api/referees").json()[0]["id"]
    review = client.post("/api/predict", json={"play_type": "offside", "referee_id": ref_id, "features": offside(30)}).json()
    rid = review["review_id"]

    link = client.post("/api/media/link", json={"review_id": rid, "url": "https://youtu.be/abcdefghijk?t=42"})
    assert link.status_code == 201 and link.json()["start_seconds"] == 42
    up = client.post(f"/api/media/upload?review_id={rid}&caption=Toma+lateral", content=PNG,
                     headers={"Content-Type": "image/png"})
    assert up.status_code == 201, up.text
    file_id = up.json()["id"]
    served = client.get(f"/api/media/{file_id}/file")
    assert served.status_code == 200 and served.content == PNG
    assert served.headers["x-content-type-options"] == "nosniff"
    assert len(client.get(f"/api/reviews/{rid}").json()["media"]) == 2

    client.post(f"/api/reviews/{rid}/decision", json={"called": True})
    play_id = client.get(f"/api/reviews/{rid}").json()["play_id"]
    play = client.get(f"/api/plays/{play_id}").json()
    assert {m["kind"] for m in play["media"]} == {"file", "link"}

    # La misma jugada aparece como similar, con sus medios.
    again = client.post("/api/predict", json={"play_type": "offside", "referee_id": ref_id, "features": offside(30)}).json()
    match = [p for p in again["similar_plays"] if p["id"] == play_id]
    assert match and len(match[0]["media"]) == 2


def test_media_permissions_and_validation(login, app):
    op = login("periodista")
    rid = op.post("/api/predict", json={"play_type": "offside", "features": offside(1)}).json()["review_id"]
    bad_type = op.post(f"/api/media/upload?review_id={rid}", content=b"MZ", headers={"Content-Type": "application/x-msdownload"})
    assert bad_type.status_code == 415
    assert op.post("/api/media/link", json={"review_id": rid, "url": "javascript:alert(1)"}).status_code == 422
    assert op.post("/api/media/link", json={"url": "https://x.com/a.mp4"}).status_code == 422
    assert op.post("/api/media/link", json={"play_id": 1, "url": "https://x.com/a.mp4"}).status_code == 403

    app.state.db.add_user("otro_periodista", auth.hash_password("clave-de-prueba"), "periodista")
    other = TestClient(app)
    other.post("/api/auth/login", json={"username": "otro_periodista", "password": "clave-de-prueba"})
    assert other.post("/api/media/link", json={"review_id": rid, "url": "https://x.com/a.mp4"}).status_code == 403

    item = op.post(f"/api/media/upload?review_id={rid}", content=PNG, headers={"Content-Type": "image/png"}).json()
    assert TestClient(app).get(f"/api/media/{item['id']}/file").status_code == 401
    assert other.delete(f"/api/media/{item['id']}").status_code == 403
    assert op.delete(f"/api/media/{item['id']}").status_code == 200
    assert op.get(f"/api/media/{item['id']}/file").status_code == 404

    editor = login("editor")
    assert editor.post("/api/media/link", json={"play_id": 1, "url": "https://x.com/a.jpg"}).status_code == 201


def test_import_with_video_column(db):
    csv_text = (
        "tipo,arbitro,cobrado,video,video_inicio,decision_campo,distancia_cm,interfiere_juego,jugada_deliberada_rival\n"
        "offside,Nuevo Juez,si,https://youtu.be/abcdefghijk,1m30s,no,5,si,no\n"
        "offside,Nuevo Juez,no,,,no,-5,si,no\n"
    )
    result = import_csv(db, csv_text)
    assert result["imported"] == 2, result
    ref_id = db.find_referee("Nuevo Juez")["id"]
    media = db.media_for(play_ids=[p["id"] for p in db.list_plays(referee_id=ref_id)])
    assert len(media) == 1 and media[0]["start_seconds"] == 90
    bad = import_csv(db, csv_text.replace("https://youtu.be/abcdefghijk", "ftp://x"), dry_run=True)
    assert bad["error_count"] == 1
