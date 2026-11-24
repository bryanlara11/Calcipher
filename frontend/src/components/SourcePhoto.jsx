const SOURCE = "https://www.instagram.com/p/DYUCaaOklAh/";
const PHOTOS = {
  "DYUCaaOklAh_5.jpg": { url: "/media/big-bite-junior-source.webp", alt: "Original menu photo showing Big Bite Junior Hotdog" },
  "DYUCaaOklAh_4.jpg": { url: "/media/big-bite-cheddar-source.webp", alt: "Original menu photo showing Big Bite Cheddar Cheese Hotdog" },
  "DYUCaaOklAh_2.jpg": { url: "/media/big-bite-jumbo-source.webp", alt: "Original menu photo showing Big Bite Jumbo Classic Hotdog" },
  "DYUCaaOklAh_7.jpg": { url: "/media/city-cafe-menu-source.webp", alt: "Original City Cafe coffee menu photo" },
};

export function brandPhoto(slug) {
  return {
    "7 eleven": { url: "/media/7-eleven-brand-source.webp", alt: "7-Eleven storefront in the original source post; branch unspecified", source: SOURCE },
    "mary grace": { url: "/media/mary-grace-brand-source.webp", alt: "Mary Grace cafe in the source post; pictured branch unspecified", source: "https://www.instagram.com/p/DYSHI0-kmWe/" },
    "tim ho wan": { url: "/media/tim-ho-wan-brand-source.webp", alt: "Tim Ho Wan at SM North EDSA in the source post; not a photo of every branch", source: "https://www.instagram.com/p/DXjQ6Htkr9h/" },
  }[slug] || null;
}

export default function SourcePhoto({ item }) {
  const photo = item.source_images?.map(name => PHOTOS[name]).find(Boolean)
    || (item.source_post_url === "https://www.instagram.com/p/DXjQ6Htkr9h/" && /baked buns/i.test(item.food_item)
      ? { url: "/media/tim-ho-wan-pork-buns-source.webp", alt: "Source screenshot showing Tim Ho Wan baked buns with BBQ pork" } : null);
  if (!photo) return null;
  return <figure className="source-photo"><a href={photo.url} target="_blank" rel="noreferrer"><img src={photo.url} alt={photo.alt} loading="lazy" /></a>
    <figcaption>Source image · Prices printed in the image may be historical.</figcaption>
  </figure>;
}
