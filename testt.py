import requests
import json
import base64
import re
import os
import sys
from typing import List, Optional, Dict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


APP_PASSWORD = "oAR80SGuX3EEjUGFRwLFKBTiris="

# ── Status detection window ─────────────────────────────────────────────────
# An event is considered "Live" if the current UTC time is:
#   - AFTER the event start time, AND
#   - BEFORE the event start time + LIVE_WINDOW_HOURS
# Events past the live window are marked "Ended" and excluded from the output.
LIVE_WINDOW_HOURS = 6


def compute_status(event_time_str: str) -> str:
    """
    Determine Live, Upcoming, or Ended from a GMT/UTC event_time string.
    Returns:
      'Live'     — event started within the last LIVE_WINDOW_HOURS
      'Upcoming' — event has not started yet
      'Ended'    — event started more than LIVE_WINDOW_HOURS ago (should be excluded)
    Accepts formats: 'YYYY-MM-DD HH:MM' or 'YYYY/MM/DD HH:MM'
    """
    if not event_time_str:
        return "Upcoming"
    try:
        clean = event_time_str[:16].replace("/", "-")
        event_dt = datetime.strptime(clean, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        end_dt = event_dt + timedelta(hours=LIVE_WINDOW_HOURS)
        if now >= end_dt:
            return "Ended"       # event window has fully passed — exclude it
        if event_dt <= now < end_dt:
            return "Live"
        return "Upcoming"
    except Exception:
        return "Upcoming"


@dataclass
class SportzxChannel:
    event_title: str
    event_id: str
    event_cat: str
    event_name: str
    event_time: str
    channel_title: Optional[str] = None
    stream_url: str = ""
    keyid: Optional[str] = None
    key: Optional[str] = None
    api: Optional[str] = None
    headers: Optional[str] = None
    referer: Optional[str] = None
    origin: Optional[str] = None


class SportzxClient:
    def __init__(self, excluded_categories: List[str] = None, timeout: int = 12):
        self.excluded_categories = set(c.lower() for c in (excluded_categories or []))
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Dalvik/2.1.0 (Linux; Android 13)",
            "Accept-Encoding": "gzip",
        })

    def _generate_aes_key_iv(self, s: str) -> tuple[bytes, bytes]:
        CHARSET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+!@#$%&="

        def u32(x: int) -> int:
            return x & 0xFFFFFFFF

        data = s.encode("utf-8")
        n = len(data)

        u = 0x811c9dc5
        for b in data:
            u = u32((u ^ b) * 0x1000193)

        key = bytearray(16)
        for i in range(16):
            b = data[i % n]
            u = u32(u * 0x1f + (i ^ b))
            key[i] = CHARSET[u % len(CHARSET)]

        u = 0x811c832a
        for b in data:
            u = u32((u ^ b) * 0x1000193)

        iv = bytearray(16)
        idx = 0
        acc = 0
        while idx != 0x30:
            b = data[idx % n]
            u = u32(u * 0x1d + (acc ^ b))
            iv[idx // 3] = CHARSET[u % len(CHARSET)]
            idx += 3
            acc = u32(acc + 7)

        return bytes(key), bytes(iv)

    def _decrypt_data(self, b64_data: str) -> str:
        if not b64_data.strip():
            return ""

        try:
            ct = base64.b64decode(b64_data)
            key, iv = self._generate_aes_key_iv(APP_PASSWORD)

            from Crypto.Cipher import AES

            cipher = AES.new(key, AES.MODE_CBC, iv)
            pt = cipher.decrypt(ct)

            pad = pt[-1]
            if 1 <= pad <= 16:
                pt = pt[:-pad]

            return pt.decode("utf-8", errors="replace")
        except Exception as e:
            print(f"Decryption error: {e}")
            return ""

    def _fetch_and_decrypt(self, url: str) -> dict:
        try:
            print(f"Fetching: {url}")
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
            encrypted = r.json().get("data", "")
            if not encrypted:
                print(f"⚠️ No 'data' field in response from {url}")
                return {}
            decrypted = self._decrypt_data(encrypted)
            if not decrypted:
                print(f"⚠️ Decryption returned empty for {url}")
                return {}
            return json.loads(decrypted)
        except requests.exceptions.Timeout:
            print(f"❌ Timeout fetching {url}")
            return {}
        except requests.exceptions.ConnectionError:
            print(f"❌ Connection error fetching {url}")
            return {}
        except Exception as e:
            print(f"❌ Fetch/decrypt failed {url}: {e}")
            return {}

    def _get_api_url(self) -> Optional[str]:
        install_url = "https://firebaseinstallations.googleapis.com/v1/projects/sportzx-7cc3f/installations"
        install_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Dalvik/2.1.0 (Linux; Android 13)",
            "X-Android-Cert": "A0047CD121AE5F71048D41854702C52814E2AE2B",
            "X-Android-Package": "com.sportzx.live",
            "x-firebase-client": "H4sIAAAAAAAAAKtWykhNLCpJSk0sKVayio7VUSpLLSrOzM9TslIyUqoFAFyivEQfAAAA",
            "x-goog-api-key": "AIzaSyBa5qiq95T97xe4uSYlKo0Wosmye_UEf6w",
        }
        install_body = {
            "fid": "eOaLWBo8S7S1oN-vb23mkf",
            "appId": "1:446339309956:android:b26582b5d2ad841861bdd1",
            "authVersion": "FIS_v2",
            "sdkVersion": "a:18.0.0"
        }

        try:
            print("📡 Getting Firebase installation token...")
            r = self.session.post(install_url, json=install_body, headers=install_headers, timeout=self.timeout)
            r.raise_for_status()
            auth_token = r.json()["authToken"]["token"]
            print("✅ Firebase token obtained")
        except Exception as e:
            print(f"❌ Firebase Install error: {e}")
            return None

        config_url = "https://firebaseremoteconfig.googleapis.com/v1/projects/446339309956/namespaces/firebase:fetch"
        config_headers = {
            "Content-Type": "application/json",
            "User-Agent": "Dalvik/2.1.0 (Linux; Android 13)",
            "X-Android-Cert": "A0047CD121AE5F71048D41854702C52814E2AE2B",
            "X-Android-Package": "com.sportzx.live",
            "X-Firebase-RC-Fetch-Type": "BASE/1",
            "X-Goog-Api-Key": "AIzaSyBa5qiq95T97xe4uSYlKo0Wosmye_UEf6w",
            "X-Goog-Firebase-Installations-Auth": auth_token,
        }

        config_body = {
            "appVersion": "2.5",
            "firstOpenTime": "2025-11-10T16:00:00.000Z",
            "timeZone": "Europe/Rome",
            "appInstanceIdToken": auth_token,
            "languageCode": "it-IT",
            "appBuild": "12",
            "appInstanceId": "eOaLWBo8S7S1oN-vb23mkf",
            "countryCode": "IT",
            "appId": "1:446339309956:android:b26582b5d2ad841861bdd1",
            "platformVersion": "33",
            "sdkVersion": "22.1.2",
            "packageName": "com.sportzx.live"
        }

        try:
            print("📡 Getting remote config...")
            r = self.session.post(config_url, json=config_body, headers=config_headers, timeout=self.timeout)
            r.raise_for_status()
            api_url = r.json().get("entries", {}).get("api_url")
            if api_url:
                print(f"✅ API URL obtained: {api_url}")
            else:
                print("❌ No api_url in remote config response")
            return api_url
        except Exception as e:
            print(f"❌ Remote Config error: {e}")
            return None

    def get_channels(self) -> List[SportzxChannel]:
        api_url = self._get_api_url()
        if not api_url:
            print("❌ Non è stato possibile ottenere l'URL API")
            return []

        channels_list: List[SportzxChannel] = []

        events_url = f"{api_url.rstrip('/')}/events.json"
        events = self._fetch_and_decrypt(events_url)

        if not isinstance(events, list):
            print(f"⚠️ Events is not a list, got: {type(events)}")
            events = []

        print(f"📊 Found {len(events)} events total")

        valid_events = [
            e for e in events
            if isinstance(e, dict) and e.get("cat") and e["cat"].lower() not in self.excluded_categories
        ]

        print(f"📊 {len(valid_events)} events after filtering")

        for event in valid_events:
            eid = event.get("id")
            if not eid:
                continue

            ch_url = f"{api_url.rstrip('/')}/channels/{eid}.json"
            channels = self._fetch_and_decrypt(ch_url)

            if not isinstance(channels, list):
                continue

            start_time = event.get("eventInfo", {}).get("startTime", "")
            event_time_full = start_time[:16].replace("/", "-") if start_time else ""

            for ch in channels:
                if not isinstance(ch, dict):
                    continue

                link = ch.get("link", "")
                if not link:
                    continue

                parts = link.split("|", 1)
                stream_url = parts[0].strip()

                keyid = key = None
                api_val = ch.get("api")
                if api_val and ":" in api_val:
                    keyid, key = api_val.split(":", 1)

                channels_list.append(SportzxChannel(
                    event_title=event.get("title", "Evento senza titolo"),
                    event_id=eid,
                    event_cat=event.get("cat", ""),
                    event_name=event.get("eventInfo", {}).get("eventName", ""),
                    event_time=event_time_full,
                    channel_title=ch.get("title"),
                    stream_url=stream_url,
                    keyid=keyid,
                    key=key,
                    api=api_val,
                ))

        return channels_list

    def save_json(self, channels: List[SportzxChannel], filename: str = "output.json") -> None:
        """
        Saves events grouped by event_id with a 'servers' array per event.
        Status (Live/Upcoming) is automatically computed from event_time (UTC/GMT).

        Output format (natively compatible with LiveEventManager mapping):
        [
          {
            "title":      "WWE Monday Night RAW",
            "category":   "WWE",
            "event_time": "2026-06-23 00:00",   ← UTC/GMT
            "status":     "Live",               ← auto-computed
            "servers": [
              { "name": "SportzX WWE", "url": "https://...", "drm_key": "" },
              { "name": "HD SERVER",   "url": "https://...", "drm_key": "keyid:key" }
            ]
          },
          ...
        ]

        Admin CP Source Mapping to use:
          - events_array_path : (leave empty — root is the array)
          - title             : title
          - category          : category
          - status            : status
          - servers_array_path: servers
          - server_name       : name
          - stream_url        : url
          - drm_key           : drm_key
        """
        # Group channels by event_id — preserve insertion order for stable output
        events_map: Dict[str, dict] = {}
        skipped_http = 0
        skipped_ended = 0

        for ch in channels:
            # Skip non-HTTPS streams
            if not ch.stream_url.startswith("https://"):
                skipped_http += 1
                continue

            eid = str(ch.event_id)

            if eid not in events_map:
                status = compute_status(ch.event_time)
                if status == "Ended":
                    # Mark as ended so all subsequent servers for this event are skipped too
                    events_map[eid] = None
                    skipped_ended += 1
                    continue
                events_map[eid] = {
                    "title":      ch.event_name or ch.event_title,
                    "category":   ch.event_cat,
                    "event_time": ch.event_time,   # UTC — kept for PHP dynamic recompute
                    "status":     status,
                    "servers":    [],
                }
            elif events_map[eid] is None:
                # This event was already marked as ended — skip its servers
                continue

            server_entry = {
                "name":    ch.channel_title or "Server",
                "url":     ch.stream_url,
                "drm_key": ch.api or "",          # "keyid:key" format, or "" for no DRM
            }
            events_map[eid]["servers"].append(server_entry)

        # Filter out None (ended) entries
        output_data = [e for e in events_map.values() if e is not None]

        # Stats
        live_count     = sum(1 for e in output_data if e["status"] == "Live")
        upcoming_count = sum(1 for e in output_data if e["status"] == "Upcoming")
        server_count   = sum(len(e["servers"]) for e in output_data)

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        print(f"✅ Saved {len(output_data)} events ({live_count} Live, {upcoming_count} Upcoming) to {filename}")
        print(f"   Total streams: {server_count}")
        print(f"⏭️  Skipped {skipped_http} non-HTTPS streams, {skipped_ended} ended events")


# ────────────────────────────────────────────────
if __name__ == "__main__":
    excluded_env = os.getenv("SPORTZX_EXCLUDED_CATEGORIES", "")
    excluded_categories = ["adult", "test", "xxx"]
    if excluded_env:
        try:
            parsed = json.loads(excluded_env)
            if isinstance(parsed, list):
                excluded_categories = [str(x).strip() for x in parsed if str(x).strip()]
            else:
                excluded_categories = [x.strip() for x in str(excluded_env).split(",") if x.strip()]
        except Exception:
            excluded_categories = [x.strip() for x in str(excluded_env).split(",") if x.strip()]

    timeout_env = os.getenv("SPORTZX_TIMEOUT", "15")
    try:
        timeout_val = int(timeout_env)
    except Exception:
        timeout_val = 15

    output_file = os.getenv("OUTPUT_FILE", "output.json")

    client = SportzxClient(
        excluded_categories=excluded_categories,
        timeout=timeout_val
    )

    print("🔄 Recupero canali...")
    try:
        canali = client.get_channels()
    except Exception as e:
        print(f"❌ Errore durante il recupero dei canali: {e}")
        sys.exit(1)

    print(f"📊 Trovati {len(canali)} canali in totale")

    if canali:
        try:
            client.save_json(canali, filename=output_file)
        except Exception as e:
            print(f"❌ Errore durante il salvataggio del JSON: {e}")
            sys.exit(1)
    else:
        print("⚠️ Nessun canale trovato (creo comunque file vuoto)")
        # Create empty JSON array so workflow doesn't fail
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump([], f)
