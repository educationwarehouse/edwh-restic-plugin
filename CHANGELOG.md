# Changelog

<!--next-version-placeholder-->

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
