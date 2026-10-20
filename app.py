@app.get("/")
def index():
    here = Path(__file__).parent
    for p in (here / "static" / "index.html", here / "index.html"):
        if p.exists():
            return FileResponse(p)
    raise HTTPException(404, "index.html tidak ditemukan.")
