"""Explicit Langfuse administration, never invoked by server startup or a model.

python -m ai_inspector.langfuse_admin check
python -m ai_inspector.langfuse_admin publish-prompts
python -m ai_inspector.langfuse_admin score --trace-id HEX --name helpful --value 1
"""
import argparse
import json
from ai_inspector.observability import telemetry
from .config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'publish-prompts', 'score'))
    parser.add_argument('--trace-id')
    parser.add_argument('--name', default='helpful')
    parser.add_argument('--value', type=float)
    args = parser.parse_args()
    tracing = telemetry(Settings.from_env().observability)
    if not tracing.client:
        print(json.dumps({'connected': False, 'reason': tracing.reason}))
        return 1
    try:
        if not tracing.client.auth_check():
            print(json.dumps({'connected': False}))
            return 1
        if args.action == 'publish-prompts':
            from .agent import SYSTEM_PROMPT
            prompts = {'inspector/orchestrator': SYSTEM_PROMPT}
            try:
                from drawing_photo_service.engine import PROMPT
                prompts['inspector/drawing-photo'] = PROMPT
            except ImportError:
                pass
            versions = {}
            for name, text in prompts.items():
                # Never overwrite/promote an existing production prompt implicitly.
                try:
                    prompt = tracing.client.get_prompt(name, label='production', max_retries=0)
                except Exception as error:
                    if getattr(error, 'status_code', None) != 404:
                        raise
                    prompt = tracing.client.create_prompt(name=name, type='text', prompt=text,
                        labels=['production'], commit_message='Initial bundled Inspector prompt')
                versions[name] = prompt.version
            print(json.dumps({'connected': True, 'prompt_versions': versions}))
        elif args.action == 'score':
            import re
            if not re.fullmatch(r'[0-9a-f]{32}', args.trace_id or '') or args.value is None:
                parser.error('--trace-id and --value are required')
            queued = tracing.score(args.trace_id, args.name, args.value)
            print(json.dumps({'connected': True, 'score_queued': queued}))
        else:
            print(json.dumps({'connected': True, 'environment': tracing.config.environment}))
        tracing.flush()
        return 0
    except Exception:
        print(json.dumps({'connected': False, 'reason': 'request_unsuccessful'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
