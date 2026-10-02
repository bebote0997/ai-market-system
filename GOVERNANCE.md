# Integration and deployment governance

These controls protect the frozen PAPER baseline and apply to changes merged
into `main`. They do not authorize a deploy, runner/scheduler activation, or
changes to the experiment.

## Required GitHub repository rules

Configure a ruleset or branch protection rule for `main` with:

- Pull requests required; direct pushes prohibited.
- At least one approving review by someone other than the pull request author.
- Approval of the most recent push required; stale approvals dismissed.
- The `unit` check from the `Tests` workflow required before merge.
- All review conversations resolved before merge.
- Force pushes and branch deletion prohibited.

These are repository settings, not workflow YAML settings. Until a repository
administrator enables them and records evidence of the active rule, governance
is documented but not enforceable at the GitHub merge boundary.

## Pull request and independent review

Every change to `main` must be proposed by pull request and reviewed by a
different person before merge. The reviewer checks scope, tests, frozen-baseline
comparability, and that trading/runtime behavior remains unchanged unless a
separately authorized exception explicitly permits it. The PR must identify:

- issue/task and scope;
- base SHA and proposed commit SHA;
- complete test result and the `Tests / unit` Actions run;
- reviewer's approval;
- whether a deployment is authorized (default: no).

The PR template requests these records. The required review rule above, not the
template checkbox, is the enforceable approval control.

## CI and deployment traceability

The `Tests` workflow runs the unit suite on pushes and pull requests. The
`unit` status must be successful for the exact commit proposed for integration.
The workflow contains no deployment job. A branch push may run tests but must
not trigger a deployment.

Deployment is a separate, explicitly authorized action. Before any deployment,
verify and record the merged PR, full merge/deploy SHA, successful Actions run
URL for that SHA, authorization, deployment identifier, deployed SHA, timestamp
(UTC), and actor. A deployment whose SHA or successful CI evidence cannot be
matched to the approved PR must not proceed. Keep the Render auto-deploy setting
off unless separately authorized; this document does not change Render.

Maintain the traceability record with the PR/deployment evidence. Do not
retroactively mark a historical deployment as CI-approved when its CI run
failed.
