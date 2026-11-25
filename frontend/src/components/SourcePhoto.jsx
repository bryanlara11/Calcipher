import catalogMedia from "../catalogMedia.json";
const SOURCE = "https://www.instagram.com/p/DYUCaaOklAh/";
const PHOTOS = {
  ...catalogMedia.images,
  "DYUCaaOklAh_5.jpg": { url: "/media/big-bite-junior-source.webp", source: SOURCE, note: "Prices pictured may be older.", alt: "Original menu photo showing Big Bite Junior Hotdog" },
  "DYUCaaOklAh_4.jpg": { url: "/media/big-bite-cheddar-source.webp", source: SOURCE, note: "Prices pictured may be older.", alt: "Original menu photo showing Big Bite Cheddar Cheese Hotdog" },
  "DYUCaaOklAh_2.jpg": { url: "/media/big-bite-jumbo-source.webp", source: SOURCE, note: "Prices pictured may be older.", alt: "Original menu photo showing Big Bite Jumbo Classic Hotdog" },
  "DYUCaaOklAh_7.jpg": { url: "/media/city-cafe-menu-source.webp", source: SOURCE, note: "Prices pictured may be older.", alt: "Original City Cafe coffee menu photo" },
};

export function brandPhoto(slug) {
  return {
    "7 eleven": { url: "/media/7-eleven-brand-source.webp", alt: "7-Eleven storefront in the original source post; branch unspecified", source: SOURCE },
    "mary grace": { url: "/media/mary-grace-brand-source.webp", alt: "Mary Grace cafe in the source post; pictured branch unspecified", source: "https://www.instagram.com/p/DYSHI0-kmWe/" },
    "tim ho wan": { url: "/media/tim-ho-wan-brand-source.webp", alt: "Tim Ho Wan at SM North EDSA in the source post; not a photo of every branch", source: "https://www.instagram.com/p/DXjQ6Htkr9h/" },
  }[slug] || catalogMedia.brands[slug] || null;
}

export default function SourcePhoto({ item }) {
  const photo = item.source_images?.map(name => PHOTOS[name]).find(Boolean)
    || (item.source_post_url === "https://www.instagram.com/p/DXjQ6Htkr9h/" && /baked buns/i.test(item.food_item)
      ? { url: "/media/tim-ho-wan-pork-buns-source.webp", source: item.source_post_url, note: "Original source screenshot.", alt: "Source screenshot showing Tim Ho Wan baked buns with BBQ pork" } : null);
  if (!photo) return null;
  const [width, height] = catalogMedia.dimensions[photo.url] || [];
  return <figure className="source-photo"><a href={photo.url} target="_blank" rel="noreferrer" aria-label={`Open full image: ${photo.alt}`}><img src={photo.url} alt={photo.alt} width={width} height={height} loading="lazy" decoding="async" /></a>
    <figcaption><span><a href={photo.url} target="_blank" rel="noreferrer">View full image ↗</a>{photo.source && <> · <a href={photo.source} target="_blank" rel="noreferrer">Image source</a></>}</span>{photo.note && <span>{photo.note}</span>}</figcaption>
  </figure>;
}
