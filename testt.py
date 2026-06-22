import requests
import json
import base64
import re
import os
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

APP_PASSWORD = "oAR80SGuX3EEjUGFRwLFKBTiris="


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
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
            encrypted = r.json().get("data", "")
            decrypted = self._decrypt_data(encrypted)
            if not decrypted:
                return {}
            return json.loads(decrypted)
        except Exception as e:
            print(f"Fetch/decrypt failed {url}: {e}")
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
            r = self.session.post(install_url, json=install_body, headers=install_headers, timeout=self.timeout)
            r.raise_for_status()
            auth_token = r.json()["authToken"]["token"]
        except Exception as e:
            print(f"Firebase Install error: {e}")
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
            r = self.session.post(config_url, json=config_body, headers=config_headers, timeout=self.timeout)
            r.raise_for_status()
            return r.json().get("entries", {}).get("api_url")
        except Exception as e:
            print(f"Remote Config error: {e}")
            return None

    def get_channels(self) -> List[SportzxChannel]:
        api_url = self._get_api_url()
        if not api_url:
            print("Non è stato possibile ottenere l'URL API")
            return []

        channels_list: List[SportzxChannel] = []

        events_url = f"{api_url.rstrip('/')}/events.json"
        events = self._fetch_and_decrypt(events_url)

        if not isinstance(events, list):
            events = []

        valid_events = [
            e for e in events
            if isinstance(e, dict) and e.get("cat") and e["cat"].lower() not in self.excluded_categories
        ]

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
        Saves all channels with HTTPS stream URLs to a JSON file.
        Non-HTTPS URLs are skipped.
        """
        output_data = []
        skipped_http = 0

        for ch in channels:
            # Skip non-HTTPS streams
            if not ch.stream_url.startswith("https://"):
                skipped_http += 1
                continue

            output_data.append({
                "event_title": ch.event_title,
                "event_id": ch.event_id,
                "category": ch.event_cat,
                "event_name": ch.event_name,
                "event_time": ch.event_time,
                "channel_title": ch.channel_title,
                "stream_url": ch.stream_url,
                "keyid": ch.keyid,
                "key": ch.key,
                "api": ch.api,
            })

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)

        print(f"✅ Saved {len(output_data)} HTTPS streams to {filename}")
        print(f"⏭️  Skipped {skipped_http} non-HTTPS streams")


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

    output_file = os.getenv("OUTPUT_FILE", "output.json")   # <-- JSON output

    client = SportzxClient(
        excluded_categories=excluded_categories,
        timeout=timeout_val
    )

    print("Recupero canali...")
    canali = client.get_channels()
    print(f"Trovati {len(canali)} canali in totale")

    if canali:
        client.save_json(canali, filename=output_file)
    else:
        print("Nessun canale trovato")
