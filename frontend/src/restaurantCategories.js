const CATEGORIES = {
  "7 eleven": "Convenience store", "army navy": "Casual dining", "coco ichibanya": "Casual dining",
  "crisostomo": "Casual dining", "marugame udon": "Casual dining", "tim ho wan": "Casual dining",
  "world chicken": "Casual dining", "contis": "Bakery", "mary grace": "Café & bakery",
  "good habits cafe": "Café & bakery", "dot coffee": "Café & bakery",
  "burger king": "Fast food & pizza", "chowking": "Fast food & pizza", "jollibee": "Fast food & pizza",
  "mang inasal": "Fast food & pizza", "mcdonalds": "Fast food & pizza", "popeyes": "Fast food & pizza", "wendys": "Fast food & pizza",
  "greenwich": "Fast food & pizza", "pizza hut": "Fast food & pizza", "shakeys": "Fast food & pizza",
};
export const restaurantCategory = pin => CATEGORIES[pin.matched_brand_slug] || "Other";
export const RESTAURANT_CATEGORIES = ["Bakery", "Café & bakery", "Casual dining", "Convenience store", "Fast food & pizza", "Other"];
