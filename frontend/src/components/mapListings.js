// OSM node and way IDs occupy separate namespaces; a missing Google ID isn't an identity.
export function branchKey(pin) {
  return pin.place_id ? `google:${pin.place_id}` : `osm:${pin.osm_type}:${pin.osm_id}`;
}
export function mergeListings(pins, results) {
  const listings = new Map(pins.map(pin => [branchKey(pin), { pin, items: [] }]));
  results.forEach((item, index) => {
    const key = branchKey(item);
    if (!listings.has(key)) listings.set(key, { pin: item, items: [] });
    listings.get(key).items.push({ item, number: index + 1 });
  });
  return listings;
}
