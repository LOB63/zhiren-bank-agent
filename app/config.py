from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
import os
import hashlib
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
CST = timezone(timedelta(hours=8))


def instance_id(db_path):
    identity=str(ROOT.resolve()).casefold()+"\0"+str(db_path.resolve()).casefold()
    return hashlib.sha256(identity.encode()).hexdigest()[:20]

@dataclass
class Settings:
    db_path: Path = ROOT / "data" / "bank.sqlite3"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    thinking: bool = True
    effort: str = "high"
    demo_date: str | None = None
    max_rounds: int = 12
    request_timeout: float = 60
    demo_scenarios: bool = False
    # Historical regression fixtures only; never loaded from environment or HTTP.
    legacy_wealth_enabled: bool = False

    @classmethod
    def from_env(cls):
        return cls(api_key=os.getenv("DEEPSEEK_API_KEY", ""),
                   base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                   model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
                   thinking=os.getenv("DEEPSEEK_THINKING", "enabled") == "enabled",
                   effort=os.getenv("DEEPSEEK_EFFORT", "high"),
                   demo_date=None if os.getenv("APP_DEMO_DATE", "system").strip().lower() in ("", "system", "auto") else os.environ["APP_DEMO_DATE"])

    @property
    def business_date(self):
        return self.demo_date or datetime.now(CST).date().isoformat()

    def now(self):
        current = datetime.now(CST)
        if not self.demo_date:
            return current
        return current.replace(year=int(self.demo_date[:4]), month=int(self.demo_date[5:7]),
                               day=int(self.demo_date[8:10]))

    def stamp(self):
        return self.now().isoformat(timespec="seconds")
