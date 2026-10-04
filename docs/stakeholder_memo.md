# Cross-sell and customer journeys: findings and recommendations

**To:** product, marketing and CRM leads. **Data:** 17 monthly snapshots of retail bank product
holdings (Santander, January 2015 to May 2016), 956,645 customers. Every number comes from the
reports named in brackets; rates carry 95% intervals in those reports.

## Three findings

**1. Most customers stall at the first product.** 65.1% of customers reach the first ladder step
(current account), but only 18.6% of those go on to direct debit, 22.0% of those to the payroll
package and 31.7% of those to a credit card: 0.8% of all customers reach the top step. The first
step is the weakest, and it is far weaker for some groups: 8.6% of university-segment customers
move from current account to direct debit, against 26.5% of individuals and 35.0% of top-tier
customers. Customers who joined through channels KHE and KHQ convert at 6.9% and 3.1%, against
about 29% for channels KAT and KFC. *(customer_analytics.md, analysis/13_ladder_funnel.csv)*

**2. Half of all "adoptions" are products switching back on, not new sales.** Of 561,710 adoption
events, 290,392 (51.7%) are products the customer had dropped earlier in the window. Payment-type
products dominate: direct debit has 92,993 repeat and 60,153 first-time adoptions, and credit card
46,473 repeat and 22,645 first-time. Adoption counts that do not separate the two overstate new
cross-sell. *(customer_analytics.md)*

**3. Engagement follows product breadth, and it is falling.** The share of active customers fell
from 53.2% in January 2015 to 42.5% in May 2016. In May 2016, 0.7% of customers holding no product
were active, against 37.5% with one product, 84.3% with two and 98.8% with three. A quarter of
customers present in May 2016 (25.2%) hold no product at all. These are associations, not proof
that adding a product makes a customer active. *(customer_analytics.md)*

## Three recommendations

**1. Make current account to direct debit the main cross-sell target**, starting with the
university segment and customers who joined through channels KHE and KHQ, where the first step
converts worst.

**2. Report first-time adoption as the cross-sell KPI** and track re-activation of payment
products (direct debit, payroll, pension payments, credit card) separately, as a servicing and
retention measure.

**3. Use the propensity model for capacity-limited credit card campaigns, with a holdout.** On the
test months (March and April 2016), the top 10% of scores contained 85.5% of all credit card
adopters, but most of those were customers who had held the card before. For genuine first-time
adopters the top 10% contained 63.1% of them, 6.3 times the average rate, from a base rate of
0.07%: even well-targeted first-time campaigns will convert a small share of contacts.
*(model_report.md)*

## Caveats

- Propensity is not uplift: the model finds likely adopters, including those who would adopt
  without contact. Money figures in the model report rest on an assumed margin and contact cost;
  the data has no revenue.
- Funnel, segment and engagement comparisons are correlational.
- The data covers a Spanish bank in 2015 to 2016, 17 months only; patterns may have changed.

## Next step

Run a randomised targeting test: score eligible customers, randomly hold out part of the top-scored
group from contact, and compare first-time adoption between contacted and held-out customers. That
measures the campaign's incremental effect, which this analysis cannot. Size the test from the
first-time base rate in the model report before launch.
