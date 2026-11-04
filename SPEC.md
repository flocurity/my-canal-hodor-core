
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
