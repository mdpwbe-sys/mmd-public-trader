# Entity and ship lookups for the New Eden map

- The selected-system panel resolves only its displayed faction, alliance, or corporation IDs through public `POST /latest/universe/names/`, then stores the result for seven days. CCP documents `/universe/names/` as the consolidated replacement for the old names routes: <https://developers.eveonline.com/blog/breaking-changes-to-characters-names-and-corporation-names>.
- Latest kills carry `victim.ship_type_id` from the existing lazy zKill response. The UI uses the public Image Service type icon URL for that ID and loads no image until a system panel displays one of its five latest kills. CCP documents the image export service: <https://developers.eveonline.com/docs/services/iec/>.
