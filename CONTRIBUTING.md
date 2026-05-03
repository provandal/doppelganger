# Contributing

Two requirements for any contribution:

## DCO sign-off

Every commit must carry a Developer Certificate of Origin sign-off. Use `git commit -s` to add the `Signed-off-by:` line.

The DCO is a lightweight contributor-provenance mechanism — it certifies you have the right to submit the contribution under the project's license. Full text at <https://developercertificate.org/>.

## License agreement

By submitting a contribution, you agree your contribution is licensed under the same terms as this repository (Apache License 2.0). See [`LICENSE`](LICENSE).

**Do not submit GPL-licensed code, or code that links against GPL libraries, into this repository.** The repository's Apache-2.0 license depends on the source tree staying GPL-free; the Docker image's "mere aggregation" defense (see [`NOTICE`](NOTICE)) depends on Doppelgänger's source not being a derivative work of NS-3. C++ patches to NS-3 itself belong in [`provandal/ns3-datacenter`](https://github.com/provandal/ns3-datacenter), not here.

## Style

Match the surrounding code. Prefer small, focused commits. Reference the relevant section of the design doc in commit messages when the change is design-load-bearing.
