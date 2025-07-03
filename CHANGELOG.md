# Changelog

<!--next-version-placeholder-->

## v0.11.2 (2025-07-03)

### Fix

* Use `edwh.task` instead of `invoke.task` ([`48c66d4`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/48c66d41807fd4d9b1de5e94a3cc047e23c8b2a7))

## v0.11.1 (2025-04-10)

### Fix

* Use OS_PROJECT_DOMAIN_NAME dynamically ([`8e245aa`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/8e245aafc01069498205d8833b40e561a6b1f463))

## v0.11.0 (2025-04-03)

### Feature

* Switch to openstack auth v3 ([`1fe480b`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/1fe480bd8add763046d2b54a6a5d75cf2e8a5f76))

## v0.10.0 (2025-03-07)

### Feature

* Started on `restic.forget` subcommand, doesn't use Policy class yet ([`dd91a08`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/dd91a08020b1b1360a9093e35b5d8585844ed27a))
* Started on restic forget policy state class that can read/write cli args and toml files ([`929a941`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/929a941f1ac562042ee34b7c2f655a98a3d7f8b0))

### Fix

* **backup:** Execute forget policy after backing up (if available and --without-forget is not used) ([`fdec720`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/fdec72059e2bd659de5cc9f1d0637676fd62b853))
* Rename 'purge' to 'prune' as restic expects that; add `du` helper for disk usage ([`96798e4`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/96798e4aeead7e53e1b1439cda46991f5c66f4e5))

### Documentation

* Add docstring to the new 'forget' and 'du' functions ([`1ffefd1`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/1ffefd17399e4e673e74ae1bfba68b34c276d367))

## v0.9.2 (2024-11-21)

### Fix

* Make `restic.run` use an actual (bash) shell instead of singular inputs (without readline/history) ;use termcolor instead of print_color; ([`0ac7c70`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/0ac7c7048c53c96ebddb03744b23f62972c599ab))

## v0.9.1 (2024-11-05)

### Fix

* **r2:** Bucket name was hardcoded ([`aebbeae`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/aebbeae64047f75014bbb6c6ef01f001562ac188))

## v0.9.0 (2024-11-05)

### Feature

* Support Cloudflare R2 as a storage backend ([`7e8e328`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/7e8e328f48e06af5dc04570a3e17213cf6bbc459))

## v0.8.0 (2024-08-20)

### Feature

* Added --command <restic subcommand> option to `restic run`, since you don't always want to enter commands interactively ([`92754fd`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/92754fdcc1411c63455893bbd555df894e211739))

## v0.7.3 (2024-04-12)

### Fix

* Add `edwh.require_sudo` to improve sudo password prompting ([`bf77993`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/bf77993be0d13773e2aad97c6109410bf4a952c7))

## v0.7.2 (2024-03-15)
### Fix
* **hooks:** Show exit code on file execution error ([`15fb3f6`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/15fb3f60b16cd0d2bb4cfd71e948e67dadbfb8a5))

## v0.7.1 (2024-03-15)
### Fix
* Output stdout and stderr live (instead of afterwards) ([`476132f`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/476132fbb0ef2ea3ade3cc5cbc7cce1dfaa319a1))

## v0.7.0 (2024-02-23)
### Feature
* `env` functie toegevoegd om zo environment variabelen te printen via de cmd ([`a57fa1a`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/a57fa1a46f01311e95f364bc4d0d3575de7afa46))

### Fix
* Auto-create env file if missing; fix heapq error for duplicate priorities (Repository instances were sortable but subclasses were not) ([`4f1d2e2`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/4f1d2e2e07b40b5511a3bcda17a8a4667d7766ed))
* Types moest hernoemd worden ([`9dd7960`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/9dd7960e52d970c6e958d5b7bcbed9f069b914f8))
* Typing-extensions toegevoegd aan dependencies ([`bf0af9d`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/bf0af9de119508fba1f152b7b35cef9c40ba7f61))

### Documentation
* Moet nog getest worden ([`c54254d`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/c54254d454205053c44e9efe24ae918f47d74151))

## v0.6.1 (2024-01-08)
### Fix
* Restic wordt nu geinstalleerd als die nog niet gevonden kan worden ([`70a25e3`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/70a25e3679580d5b3c71f2523d62b7b65bf2f67f))

## v0.6.0 (2023-11-14)
### Feature
* **repo:** Added s3 and s3-compat Oracle storage ([`fbba0ca`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/fbba0caac3b145943e61ba94018f74a5eb306782))

### Fix
* Update repository local settings on check_env ([`9c6e3b4`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/9c6e3b4f3ade9224b48fe63cb4734c3855826076))
* **self-update:** Improved restic self-update handling ([`a69978c`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/a69978c5b4040fec04fb2a0debbf04b868d29d98))

### Documentation
* Mentioned S3 ([`a782daf`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/a782daf0146c808c4a259e1459a585d24f132f9f))

## v0.5.0 (2023-10-05)
### Feature
* Captain hooks scripts now also have access to .env settings ([`a92e4c5`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/a92e4c588249292f30fe5969d73f79ddf890d6fe))

### Documentation
* Manually release 0.4.0 ([`606ec48`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/606ec48348ffce6965862a3a33865572b659233e))

## v0.4.0 (2023-09-27)
### Fix
* Exclude venv from git and pip build ([`e15cce6`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/e15cce677b34a2b5b6c954bb4c4ff07727cda6c7))
* Use dynamic `docker compose` command via edwh (pass --old-compose to use `docker-compose` instead) ([`a3214f8`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/a3214f82e8260842d296ea1012d553faa0c36e94))

### Documentation
* **changelog:** Merge beta release logs into 0.3.3 ([`fa1afe3`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/fa1afe3272ec4a52e58779e8dbe08e9a791f006a))

## v0.3.3 (2023-08-01)

### Fix
* restic.snapshots prints again ([`ee153e4`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/ee153e495c60696b12d38496d80817c37394f217))

### Refactor
* improved minor issues with code, better hints etc.


## v0.3.2 (2023-06-27)
### Fix
* Added expanduser() to check_env to make `~/` work ([`7d5fcd5`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/7d5fcd5eae40943f0d4e2143e89ea761373fec4a))

## v0.3.1 (2023-06-20)
### Fix
* Fixed color coding in restic plugin. now doesn't only print in white but in set color of the cmd ([`80ad277`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/80ad27717a89c9843b62d002e214493ad428c39c))

## v0.3.0 (2023-06-09)
### Feature
* Edwh-restic-plugin now exist with the highest status code one of the given shell scripts gave it ([`281f600`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/281f600082cba3853b1bc35efda93ea50ea8f3b5))

## v0.2.7 (2023-06-09)
### Fix
* Made it now so it gets the result is captured even when throwing exception ([`fad076a`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/fad076aab666925c2bb795a2705500215f7500c5))

## v0.2.6 (2023-06-09)
### Fix
* Made it so the tasks.py doesn't throw an error when the shell throws an error. this is done by adding hide=verbose and removing the if vebose.... the reason also why we aren't using pty=true and warn=true is because pty=true NEVER prints to stderr(see https://docs.pyinvoke.org/en/stable/api/runners.html#invoke.runners.Runner.run and the the pty part) ([`ff4f819`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/ff4f819a4df93889aad3b6155373cc17b42dcc02))

## v0.2.5 (2023-06-09)
### Fix
* Fixed that the status codes of the [succes] and [failure] are a bit clearer ([`1d3fe77`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/1d3fe773a7481811efff23ec0a5eade565c91caa))

## v0.2.4 (2023-06-09)
### Fix
* Fixed a bug where it prints error and succes multiple times for the same file ([`4bfa7d2`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/4bfa7d27c50097619a2620b6f38b27a0f217eb04))

## v0.2.3 (2023-06-09)
### Fix
* Small updates to color-coding and clearer prints. also fixed a bug where a error will print for every sh script that failed ([`5c9f164`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/5c9f1642ae25c084a426592fd6c4fca40ca9d5d7))

## v0.2.2 (2023-06-09)
### Fix
* Made verbosity better when running files using backup or restore ([`cf37f24`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/cf37f2480e188a58a79c33a1e3cf4ba2ce2802c9))

## v0.2.1 (2023-06-09)
### Fix
* Automatically using verbose and now always hiding restic message ([`7d6537f`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/7d6537f800230c076b07996e454556620e6888c8))

## v0.2.0 (2023-05-24)
### Feature
* Added restic.run, this sets up a restic enviroment with the connection choice of your choosing. this allows you to run restic commands without giving up some env variables to restic(like the $HOST or $URI). every command that is ran prints the stdout and err to the screen ([`9199495`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/9199495bc2c70bf11fbd16fc7c56864b7fff99a3))

## v0.1.7 (2023-05-24)
### Fix
* Added pty for verbosity sometimes not giving output ([`c876af0`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/c876af0fa60626837e56b4d643366cb30966212f))

## v0.1.6 (2023-05-24)
### Fix
* Added error logs when giving up -v ([`0667e45`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/0667e45e52aff335f74c1712c7ebb8bba3638751))

## v0.1.5 (2023-05-24)
### Fix
* Added enviroment variables HOST AND URI for restic(automaticly see https://restic.readthedocs.io/en/latest/040_backup.html?highlight=environment#environment-variables for more). ([`a836553`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/a836553c5478511c9c57416b441e027ad12df9fc))
* Merge van sh files ([`20c198f`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/20c198ffab40165cf7a50b9dcce98a62b719ed82))
* Added verbosity to the script output ([`30a824e`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/30a824e8f77f854fe9c201009f4013d23474f5d7))

### Documentation
* Removed spelling mistake in readme(restore -> backup) ([`31efedb`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/31efedb7e99013c39db45a42e31cdfeaf8e24394))

## v0.1.4 (2023-05-11)


## v0.1.3 (2023-05-11)
### Fix
* Remove sudo chmod +x *.sh because owasp risks ([`84fa3ed`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/84fa3ed29f6d154ea7b2f24e0bd632e53a4bdc04))
* Some .sh files cannot be executed because they don't have the executable permission ([`f4361a5`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/f4361a54b5487e35a9bacd5982e8dadabbf2e286))
* Fixed restore doing a backup and fixed some docs ([`b8175a1`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/b8175a1a75a2ce9af6b84e8eb50bc6701d5ea9c6))
* Improved examples for stream backup and restore ([`ccedf74`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/ccedf74649bdebc179b97ac0a0d0c42a14776816))

## v0.1.2 (2023-05-02)

### Feature
* Added plugin infrastructure ([`1f08a8c`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/1f08a8c9b6363a826f2f4ca23d69426f7d8d83b1))

### Fix
* Improved examples for stream backup and restore ([`0178dae`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/0178daeb18d2eadc9ae0d8ef9ac948fbc379b2a1))

### Documentation
* Restructuring readme ([`b0a6362`](https://github.com/educationwarehouse/edwh-restic-plugin/commit/b0a6362ad861ce597e2864d30733e07688ab60be))
