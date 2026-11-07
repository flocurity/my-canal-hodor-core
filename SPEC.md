
Explicit profile bootstrap: `PassIdAuth.bootstrap(profile_id=...)` authenticates
only a profile returned by the fresh validated Profiles response. It never prompts
or creates, deletes or rewrites the remembered CLI profile. Calling `bootstrap()`
without arguments preserves the original interactive selection and delayed
preference invalidation. Failure header side effects are unchanged: a successful
renewal remains applied; selection updates the profile header before authenticate.
A failed authentication does not silently roll back headers or persist preference.

`CanalClient.set_playlist` is a constrained PUT/DELETE for one exact content ID
and explicit authenticated profile context. It accepts the observed empty 204
contract, disables redirects and does not retry ambiguous mutations. Applications
must confirm server state separately before persisting local favorite state.

Episode catalogs support direct programme membership through an absent season
(`Season(None, None)`, no selector) and optional episode numbers. Invalid explicit
numbers and contradictory present season coordinates remain rejected. Episode
identity and Hodor list order are independent of editorial numbering; no technical
number is generated from an ID. Season numbers need not be unique when IDs are.
Unstructured Cookie/Set-Cookie header lines remain masked, while a programme title
containing “cookie :” is preserved. Structured secret keys and registered secret
values remain redacted independently.

Technical seasons without a selector retain the caller-validated season identity.
Absent season numbers remain None; contradictory or invalid structured coordinates
are rejected. Season.implicit distinguishes an implicit episode list from a
visible numbered selector. Pagination and list order validations remain unchanged.
