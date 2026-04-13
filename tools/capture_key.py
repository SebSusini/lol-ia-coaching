"""
mitmproxy addon to capture the GAMHS encryption key
Run with: mitmdump -s capture_key.py -p 8899 --ssl-insecure

Then set system proxy to localhost:8899 and launch LeagueClient.
When a replay is launched, this script captures the encryption key.
"""

import json
import mitmproxy.http


class KeyCapture:
    def __init__(self):
        self.found_keys = []

    def response(self, flow: mitmproxy.http.HTTPFlow):
        url = flow.request.pretty_url

        # Log ALL requests to see what endpoints are hit
        print(f"[{flow.response.status_code}] {flow.request.method} {url[:120]}")

        # Look for replay/game/spectator related endpoints
        keywords = ["replay", "metadata", "gamhs", "spectator", "game", "observer", "download"]
        if any(kw in url.lower() for kw in keywords):
            print(f"\n{'='*60}")
            print(f"!!! REPLAY-RELATED: {url}")
            print(f"{'='*60}")

            # Try to parse response as JSON
            try:
                body = flow.response.get_text()
                data = json.loads(body)

                # Search for key-like fields
                self._find_keys(data, "")

                # Save full response
                filename = f"/tmp/gamhs_response_{len(self.found_keys)}.json"
                with open(filename, "w") as f:
                    json.dump(data, f, indent=2)
                print(f"Full response saved to {filename}")

            except (json.JSONDecodeError, Exception) as e:
                # Not JSON, check headers
                print(f"Response not JSON ({e}), checking headers...")
                for k, v in flow.response.headers.items():
                    if any(x in k.lower() for x in ["key", "encrypt", "token"]):
                        print(f"  HEADER: {k} = {v}")

        # Also check ALL JSON responses for encryption key fields
        content_type = flow.response.headers.get("content-type", "")
        if "json" in content_type:
            try:
                body = flow.response.get_text()
                if any(x in body.lower() for x in ["encryptionkey", "gamekey", "observerkey", "observerencryptionkey"]):
                    print(f"\n{'!'*60}")
                    print(f"!!! KEY FOUND IN: {url}")
                    print(f"{'!'*60}")
                    data = json.loads(body)
                    self._find_keys(data, "")

                    with open("/tmp/ENCRYPTION_KEY_FOUND.json", "w") as f:
                        json.dump(data, f, indent=2)
                    print(f"SAVED TO /tmp/ENCRYPTION_KEY_FOUND.json")
            except:
                pass

    def _find_keys(self, obj, path):
        if isinstance(obj, dict):
            for k, v in obj.items():
                full = f"{path}.{k}" if path else k
                if any(x in k.lower() for x in ["key", "encrypt", "token", "secret", "observer"]):
                    print(f"  >>> {full} = {repr(v)[:200]}")
                    self.found_keys.append({"path": full, "value": v})
                self._find_keys(v, full)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                self._find_keys(v, f"{path}[{i}]")


addons = [KeyCapture()]
