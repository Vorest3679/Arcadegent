Use this skill when runtime state includes `context_payload.route`.

Interpretation rules:
- Read `directory` first, then read `route`.
- `route.distance_m` is route distance in meters.
- `route.duration_s` is route duration in seconds; convert it to approximate whole minutes for the reply.
- Prefer `route.destination_name` as the route destination label.
- Use `shop_details.basic` only when destination context such as address or region helps the answer.
- Use `shop_details.transport` or `shop_details.comment` only when they add concrete value beyond the route itself.
- Include `route.hint` only when it adds concrete navigation value.
- If the route is missing but a shop is known, ask only for the missing navigation input instead of inventing a path.
