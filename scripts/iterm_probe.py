#!/usr/bin/env python3
"""Manual iTerm2 API probe, prepared but NOT live-tested by this agent.

Lists IDs only by default. --activate explicitly selects an exact live session.
It never types into a terminal or reads terminal contents. Activation alone does
not prove that a particular coding-agent process still owns the pane.
"""
import argparse
import json
import sys
from pathlib import Path

from session_binding import DEFAULT_ROOT, validate


def choose_session(candidate, live_ids):
    # Environment IDs may contain a window/tab prefix. Only accept candidates
    # that exactly occur in the live API inventory, never title or ordinal matches.
    candidates = {candidate, candidate.rsplit(':', 1)[-1]}
    matches = candidates.intersection(live_ids)
    if len(matches) != 1:
        raise ValueError('session missing or ambiguous')
    return matches.pop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session-id')
    parser.add_argument('--activate', action='store_true')
    parser.add_argument('--run-id')
    parser.add_argument('--agent-session-id')
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    if bool(args.run_id) != bool(args.agent_session_id):
        parser.error('--run-id and --agent-session-id must be supplied together')
    if args.run_id and args.session_id:
        parser.error('Use either a managed binding or a raw pane ID')
    if args.activate and not (args.session_id or args.run_id):
        parser.error('--activate requires a pane ID or managed binding')
    try:
        import iterm2
    except ImportError:
        parser.exit(1, 'Run with an interpreter containing the official iterm2 package.\n')

    async def run_checked(connection):
        app = await iterm2.async_get_app(connection)
        ids = [session.session_id for window in app.windows for tab in window.tabs
               for session in tab.sessions]
        report = {'live_session_ids': ids, 'activation_requested': args.activate,
                  'agent_ownership_verified': False}
        candidate = args.session_id
        binding = None
        if args.run_id:
            binding = validate(args.state_dir, args.run_id, args.agent_session_id)
            candidate = binding['pane']
            report['managed_binding_verified'] = True
        if candidate:
            selected = choose_session(candidate, set(ids))
            report['matched_session_id'] = selected
            if args.activate:
                session = app.get_session_by_id(selected, include_buried=False)
                if session is None:
                    raise ValueError('session closed before activation')
                if binding:
                    latest = validate(args.state_dir, args.run_id, args.agent_session_id)
                    if latest != binding:
                        raise ValueError('binding changed before activation')
                await session.async_activate(select_tab=True, order_window_front=True)
                report['activation_call_completed'] = True
                if binding:
                    validate(args.state_dir, args.run_id, args.agent_session_id)
                    report['agent_ownership_verified'] = True
        print(json.dumps(report, indent=2))

    result_code = 0

    async def run(connection):
        nonlocal result_code
        try:
            await run_checked(connection)
        except ValueError as error:
            result_code = 1
            print(json.dumps({'status': 'refused', 'reason': str(error)}), file=sys.stderr)

    iterm2.run_until_complete(run)
    return result_code


if __name__ == '__main__':
    sys.exit(main())
