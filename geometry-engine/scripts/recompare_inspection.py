"""Recompare saved contours and create a new report, without reconstruction/SAM."""
import argparse
import json

import httpx
from supabase import ClientOptions, create_client

from geometry_engine_api.database.connection import get_engine
from geometry_engine_api.services.archive import Settings
from geometry_engine_api.services.artifacts import ArtifactStore, SupabaseObjectStore
from geometry_engine_api.services.recomparison import RecomparisonService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job_id', help='Completed inspection UUID')
    args = parser.parse_args()
    settings = Settings.from_env()
    with httpx.Client(timeout=120, trust_env=False) as http:
        client = create_client(settings.supabase_url, settings.supabase_secret_key,
            options=ClientOptions(httpx_client=http, auto_refresh_token=False, persist_session=False))
        artifacts = ArtifactStore(SupabaseObjectStore(client, settings.storage_bucket, http_client=http))
        result = RecomparisonService(get_engine(), artifacts, settings.data_dir).run(
            args.job_id, progress=lambda message: print(message, flush=True))
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
