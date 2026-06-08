# CMapper HTTPS Deployment

HTTPS is the default runtime mode.

## Recommended: reverse proxy

Run CMapper on localhost HTTP and let Nginx/Caddy terminate TLS.

```bash
export HOST=127.0.0.1
export PORT=5000
export USE_SSL=0
export BEHIND_HTTPS_PROXY=1
export TRUST_PROXY_HEADERS=1
export SECRET_KEY='change-this-long-random-value'
python3 Backend.py
```

Use `deployment/nginx/cmapper.conf` as a starting point.

Important settings:
- `BEHIND_HTTPS_PROXY=1` makes browser session cookies secure.
- `TRUST_PROXY_HEADERS=1` lets Flask understand `X-Forwarded-Proto: https`.
- `USE_SSL=0` is safe only here because Flask is bound to localhost and Nginx owns public HTTPS.
- Keep module traffic unchanged. Modules still call LAN devices over SSH, Telnet, HTTP Digest, etc.

## Direct Flask HTTPS

Useful for quick testing or a lab server without Nginx.

Self-signed adhoc certificate:

```bash
export HOST=0.0.0.0
export PORT=5000
export SECRET_KEY='change-this-long-random-value'
python3 Backend.py
```

`USE_SSL=1` is the default. Set it explicitly only if you want to be obvious in service files.

Specific certificate:

```bash
export USE_SSL=1
export SSL_CERT_FILE=/etc/cmapper/cmapper.crt
export SSL_KEY_FILE=/etc/cmapper/cmapper.key
python3 Backend.py
```

Browsers will warn if the certificate is self-signed or does not match the hostname/IP.

## Agent URL

If agents are used, update Settings -> Agent Server URL to the HTTPS address users/agents should reach, for example:

```text
https://10.192.92.10
```

Existing saved `http://127.0.0.1:5000` settings are migrated to `https://127.0.0.1:5000` on startup.
For real agents, update this to the server's reachable HTTPS address.

## Common Problems

- Login loops: the user is opening HTTP while secure cookies are enabled. Open the HTTPS URL.
- Broken icons/downloads: check for hardcoded `http://` URLs in settings or custom files.
- Wrong redirects behind Nginx: make sure `X-Forwarded-Proto` is set and `TRUST_PROXY_HEADERS=1`.
- Browser warning: install/trust the certificate or use an internal CA certificate.

## Web Client IP Allowlist

CMapper can restrict only incoming browser/API clients using `allowed_web_ips.txt` in the project directory.

Example:

```text
127.0.0.1
10.192.3.0/24
10.192.92.10
```

If the file is missing or contains only comments/blank lines, every web client is allowed.

Blocked clients are tarpitted instead of immediately receiving `403`; by default they wait 120 seconds and then receive a plain empty response. Change that with:

```bash
export WEB_IP_BLOCK_DELAY_SECONDS=120
```

This affects only incoming web requests to Flask. It does not block outgoing module connections from the CMapper server to Cisco, MikroTik, APs, NVRs, or other nodes.
