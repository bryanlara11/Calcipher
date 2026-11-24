import { BrandLogo, Icon } from "./Brand.jsx";

export default function HomePage({ headingRef }) {
  return <div className="home-page">
    <section className="home-hero">
      <div><h1 ref={headingRef} tabIndex={-1}>Find food that fits your day.</h1>
        <p className="page-lead">Explore nearby restaurants, compare menu nutrition and save the places you want to try.</p>
        <div className="home-actions"><a className="button-primary" href="#/search">Find a meal<Icon name="arrow" size={18} /></a><a className="button-secondary" href="#/nearby">Explore nearby<Icon name="pin" size={18} /></a></div>
      </div><BrandLogo className="home-logo" />
    </section>
    <section className="how-it-works" aria-labelledby="how-heading"><h2 id="how-heading">How it works</h2>
      <ol className="how-grid">
        <li><Icon name="pin" size={26} /><h3>Choose your area</h3><p>Move the map pin or use your location. Nearby shows branches linked to our menu database.</p></li>
        <li><Icon name="filter" size={26} /><h3>Set your goals</h3><p>Search for a dish or restaurant. Add calorie, protein or budget limits when you need them.</p></li>
        <li><Icon name="heart" size={26} /><h3>Compare and save</h3><p>Check the source, open the branch on the map and favorite restaurants for later.</p></li>
      </ol>
    </section>
    <section className="home-data-note"><img src="/brand/approved-seal.webp" alt="Example CalCipher Approved seal" width="120" height="90" />
      <div><h2>A badge based on your goals</h2><p>A reviewed meal earns the Healthy badge when it meets your selected nutrition goals. Pending matches use a text label until their source values are reviewed.</p></div>
    </section>
    <section className="home-data-link"><div><h2>About menu data</h2><p>See what is in the catalog, how records are reviewed and where nutrition values come from.</p></div><a href="#/data" className="button-secondary">Read about the data<Icon name="arrow" size={17} /></a></section>
  </div>;
}
