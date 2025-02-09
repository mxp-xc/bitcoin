# -*- coding: utf-8 -*-

if __name__ == '__main__':
    import uvicorn

    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=18293,
        use_colors=True
    )
