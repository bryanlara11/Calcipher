const CATEGORIES = {
  "7 eleven": "Convenience store", "army navy": "Casual dining", "coco ichibanya": "Casual dining",
  "crisostomo": "Casual dining", "marugame udon": "Casual dining", "tim ho wan": "Casual dining",
  "world chicken": "Casual dining", "contis": "Bakery", "mary grace": "Café & bakery",
  "good habits cafe": "Café & bakery", "dot coffee": "Café & bakery",
  "burger king": "Fast food", "chowking": "Fast food", "jollibee": "Fast food",
  "mang inasal": "Fast food", "mcdonalds": "Fast food", "popeyes": "Fast food", "wendys": "Fast food",
  "greenwich": "Pizza", "pizza hut": "Pizza", "shakeys": "Pizza",
};
export const restaurantCategory = pin => CATEGORIES[pin.matched_brand_slug] || "Other";
export const RESTAURANT_CATEGORIES = ["Bakery", "Café & bakery", "Casual dining", "Convenience store", "Fast food", "Pizza", "Other"];
