# Screenshot demo

The pictures in `docs/images/` come from this demo, not from a real install. `demo.py` starts the A2RM web UI in
a throwaway container with made-up discs: invented titles, posters and stills generated with ffmpeg, a library of
tiny generated video files (sparse, so they report real lengths and sizes but take a few MB), and fixed demo values
for the TMDb search, the hardware check and the server stats. Nothing is sent anywhere and nothing outside the
container is touched.

## Run it

```sh
docker run --rm -p 8095:8095 --entrypoint "" -e DEMO_URL=http://<host>:8095 \
    -v "$PWD/scripts/screenshot-demo:/demo:ro" arm:latest python3 /demo/demo.py
```

Open `http://<host>:8095` once it prints `A2RM demo on port 8095`. `DEMO_URL` is the address your browser uses
(search-result posters need a full http address). Stop it with `docker rm -f <container>` or Ctrl+C.

## Take the screenshots

Headless Chrome at 1280 px wide, for example:

```sh
chrome --headless=new --hide-scrollbars --window-size=1280,860 --virtual-time-budget=12000 \
    --screenshot=home.png http://<host>:8095/
```

Pages used: `/`, `/jobdetail?job_id=12&q=Copper%20Valley` (the Identify search runs from `?q=`; don't add an
`#anchor`, headless Chrome scrolls mid-capture), `/library`, `/library/files`, `/automation/health`,
`/automation/settings`, `/automation/hardware`, plus the job page at phone width. Crop and convert with ffmpeg:

```sh
ffmpeg -i home.png -vf crop=1280:830:0:0 -c:v libwebp -quality 82 docs/images/home.webp
```
