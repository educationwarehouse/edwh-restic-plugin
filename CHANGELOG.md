# Changelog

<!--next-version-placeholder-->

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
