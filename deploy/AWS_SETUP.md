# CI/CD with GitHub Actions, S3 and EC2

```
Code change -> git push -> GitHub Actions
   1. Unit tests        (fails the pipeline if any test fails)
   2. Security scan     Bandit (SAST) + pip-audit (dependencies) + Gitleaks (secrets)
   3. Build             build-v<VERSION>-<run>.zip
   4. Store             uploaded to S3: builds/build-v1.0.0-7.zip and builds/latest.zip
   5. Deploy            EC2 downloads builds/latest.zip from S3, installs it, restarts, checks /health
   6. Verify            the live site shows the new version
```

- **No AWS keys in GitHub.** GitHub logs in to AWS with OIDC and gets short-lived credentials.
- **No SSH.** Deployment runs through AWS Systems Manager (SSM). Only port 80 is open.
- **Rollback.** If the new release fails its health check, the previous release is started again.

Use one AWS region for everything (for example `ap-south-1`).

## Part A: AWS console

### 1. S3 bucket for the builds
S3 -> Create bucket. Name must be unique, for example `docflow-builds-<yourname>-2026`.
Leave "Block all public access" ON. Write the name down.

### 2. Security group
EC2 -> Security Groups -> Create. Name `docflow-sg`. Inbound: HTTP, port 80, Anywhere-IPv4. No SSH rule.

### 3. Role for the server
IAM -> Roles -> Create role -> AWS service -> EC2. Attach `AmazonSSMManagedInstanceCore`.
Name it `docflow-ec2-role`. Open it -> Add permissions -> Create inline policy -> JSON: paste
`deploy/iam/ec2-s3-read-policy.json` with `<BUCKET_NAME>` replaced. Name it `read-builds`.

### 4. Launch the server
EC2 -> Launch instance.
- Name `docflow-server`, image **Amazon Linux 2023**, type `t3.micro` (free tier eligible)
- Key pair: Proceed without a key pair
- Network: security group `docflow-sg`, auto-assign public IP **Enable**
- Advanced -> IAM instance profile `docflow-ec2-role`
- Advanced -> User data: paste all of `deploy/ec2-bootstrap.sh`

Wait 2-3 minutes. Write down the **Instance ID** and the **Public IPv4 address**.
Systems Manager -> Fleet Manager should list the instance (can take a few minutes).

### 5. Let GitHub log in to AWS (one time)
IAM -> Identity providers -> Add provider -> OpenID Connect.
Provider URL `https://token.actions.githubusercontent.com`, audience `sts.amazonaws.com`.

### 6. Role for GitHub Actions
IAM -> Roles -> Create role -> Web identity -> provider `token.actions.githubusercontent.com`,
audience `sts.amazonaws.com`, your GitHub username, your repo name, branch `main`.
Skip permissions, name it `docflow-github-actions`. Then open it:
- Add permissions -> Create inline policy -> JSON: paste `deploy/iam/github-permissions-policy.json`
  with `<BUCKET_NAME>`, `<REGION>`, `<ACCOUNT_ID>`, `<INSTANCE_ID>` replaced. Name it `docflow-deploy`.
- Trust relationships should match `deploy/iam/github-trust-policy.json`.
- Copy the role **ARN**.

## Part B: GitHub

### 7. Create the repository
github.com -> New repository (e.g. `docflow`), empty, no README.

### 8. Add settings (BEFORE the first push)
Repository -> Settings -> Secrets and variables -> Actions.

Variables:

| Name | Value |
| --- | --- |
| `AWS_REGION` | your region |
| `S3_BUCKET` | the bucket name from step 1 |
| `EC2_INSTANCE_ID` | from step 4 |
| `EC2_HOST` | public IPv4 address from step 4 |

Secret: `AWS_ROLE_ARN` = the role ARN from step 6.

### 9. Push (PowerShell, inside the project folder)
```powershell
git init
git branch -M main
git add .
git commit -m "DocFlow with CI/CD pipeline"
git remote add origin https://github.com/<USER>/<REPO>.git
git push -u origin main
```
The first run deploys version 1.0.0.

## Part C: The demo change
1. Edit `VERSION` to `1.1.0` and change a visible line in `static/index.html` (for example the subtitle text).
2. `git add . && git commit -m "v1.1.0" && git push`
3. Actions tab: Unit tests + Security scan -> Build and upload to S3 -> Deploy.
4. S3 console -> bucket -> `builds/`: `build-v1.1.0-<n>.zip` and `latest.zip`.
5. Open `http://<EC2_HOST>/`: the page shows `v1.1.0` and your text change.

Failing test demo (optional): change `10.0` to `11.0` in `tests/test_flow.py`, push. The test job fails,
nothing is built or deployed. Revert and push again.

## 2-minute video plan

| Time | Show |
| --- | --- |
| 0:00-0:15 | Live site showing the old version, then edit `VERSION` + `index.html` in the editor |
| 0:15-0:30 | `git commit` and `git push` in the terminal |
| 0:30-1:15 | Actions run: Unit tests, Security scan (Bandit, pip-audit, Gitleaks), Build. Fast-forward the waiting |
| 1:15-1:30 | S3 console: the new `build-v...zip` and `latest.zip` |
| 1:30-1:45 | Deploy job log: "Downloading s3://...", "Deployed version 1.1.0 ... healthy" |
| 1:45-2:00 | Refresh the live site: new version and the changed text |

Record the old-version page BEFORE you push.

## If something fails

| Symptom | Likely cause |
| --- | --- |
| `Not authorized to perform sts:AssumeRoleWithWebIdentity` | Trust policy repo name or branch is wrong (case-sensitive), or the secret `AWS_ROLE_ARN` is missing |
| `AccessDenied` on S3 upload | `<BUCKET_NAME>` in the GitHub role policy is wrong |
| `InvalidInstanceId` / not in Fleet Manager | Server role lacks `AmazonSSMManagedInstanceCore`, or wait a few minutes after launch |
| Deploy output: `python3: command not found` or `unzip: command not found` | User data did not run. Session Manager -> read `/var/log/cloud-init-output.log` |
| Deploy output: `AccessDenied` on download | Server role is missing the `read-builds` inline policy |
| Gitleaks job fails | A secret-looking string is committed. Remove it and rotate it |
| Page does not load | Use `http://` not `https://`; check security group port 80 |
| Verify step fails but deploy passed | `EC2_HOST` is out of date (address changes after stop/start) |

Server shell without SSH: Systems Manager -> Session Manager -> Start session.
Logs: `sudo journalctl -u docflow -n 50`.

## Limits
- One server only: the queue and cache live inside the process.
- Demo login and plain HTTP. Terminate the instance after grading.

## Clean up
Terminate the EC2 instance, empty and delete the S3 bucket, delete the IAM roles and the GitHub OIDC provider.
