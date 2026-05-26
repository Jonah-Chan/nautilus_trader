// -------------------------------------------------------------------------------------------------
//  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
//  https://nautechsystems.io
//
//  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
//  You may not use this file except in compliance with the License.
//  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.
// -------------------------------------------------------------------------------------------------

//! OKX-specific custom data types.

use std::sync::Arc;

use nautilus_core::UnixNanos;
use nautilus_model::{
    data::{CustomData, DataType, OptionGreekValues, OptionGreeks},
    enums::GreeksConvention,
    identifiers::InstrumentId,
};
use nautilus_persistence_macros::custom_data;

/// Venue-provided OKX option greeks carried as Rust custom data.
#[cfg_attr(feature = "arrow", custom_data(pyo3))]
#[cfg_attr(not(feature = "arrow"), custom_data(pyo3, no_arrow))]
pub struct VenueOptionGreeks {
    pub instrument_id: InstrumentId,
    pub delta: f64,
    pub gamma: f64,
    pub vega: f64,
    pub theta: f64,
    pub rho: f64,
    pub mark_iv: f64,
    pub bid_iv: f64,
    pub ask_iv: f64,
    pub underlying_price: f64,
    pub open_interest: f64,
    pub convention: String,
    pub ts_event: UnixNanos,
    pub ts_init: UnixNanos,
}

impl VenueOptionGreeks {
    /// Creates venue-specific custom data from the standard model `OptionGreeks`.
    #[must_use]
    pub fn from_option_greeks(greeks: &OptionGreeks) -> Self {
        Self {
            instrument_id: greeks.instrument_id,
            delta: greeks.delta,
            gamma: greeks.gamma,
            vega: greeks.vega,
            theta: greeks.theta,
            rho: greeks.rho,
            mark_iv: greeks.mark_iv.unwrap_or_default(),
            bid_iv: greeks.bid_iv.unwrap_or_default(),
            ask_iv: greeks.ask_iv.unwrap_or_default(),
            underlying_price: greeks.underlying_price.unwrap_or_default(),
            open_interest: greeks.open_interest.unwrap_or_default(),
            convention: match greeks.convention {
                GreeksConvention::BlackScholes => "BLACK_SCHOLES".to_string(),
                GreeksConvention::PriceAdjusted => "PRICE_ADJUSTED".to_string(),
            },
            ts_event: greeks.ts_event,
            ts_init: greeks.ts_init,
        }
    }

    /// Converts venue-specific greeks into the standard model `OptionGreeks`.
    #[must_use]
    pub fn to_option_greeks(&self) -> OptionGreeks {
        OptionGreeks {
            instrument_id: self.instrument_id,
            convention: self
                .convention
                .parse::<GreeksConvention>()
                .unwrap_or_default(),
            greeks: OptionGreekValues {
                delta: self.delta,
                gamma: self.gamma,
                vega: self.vega,
                theta: self.theta,
                rho: self.rho,
            },
            mark_iv: Some(self.mark_iv),
            bid_iv: Some(self.bid_iv),
            ask_iv: Some(self.ask_iv),
            underlying_price: Some(self.underlying_price),
            open_interest: Some(self.open_interest),
            ts_event: self.ts_event,
            ts_init: self.ts_init,
        }
    }
}

impl VenueOptionGreeks {
    /// Wraps this value in a [`CustomData`] envelope for the data engine.
    #[must_use]
    pub fn into_custom_data(self) -> CustomData {
        let instrument_id = self.instrument_id.to_string();
        CustomData::new(
            Arc::new(self),
            DataType::new("VenueOptionGreeks", None, Some(instrument_id)),
        )
    }
}

#[cfg(feature = "python")]
#[pyo3::pymethods]
impl VenueOptionGreeks {
    #[pyo3(name = "to_option_greeks")]
    fn py_to_option_greeks(&self) -> OptionGreeks {
        self.to_option_greeks()
    }
}

/// Registers OKX custom data types.
pub fn register_okx_custom_data() {
    #[cfg(feature = "arrow")]
    nautilus_serialization::ensure_custom_data_registered::<VenueOptionGreeks>();

    #[cfg(not(feature = "arrow"))]
    let _ = nautilus_model::data::ensure_custom_data_json_registered::<VenueOptionGreeks>();

    let _ = nautilus_model::data::register_option_greeks_bridge("VenueOptionGreeks", |data| {
        data.as_any()
            .downcast_ref::<VenueOptionGreeks>()
            .map(VenueOptionGreeks::to_option_greeks)
    });
}

#[cfg(test)]
mod tests {
    use rstest::rstest;

    use super::*;

    #[rstest]
    fn test_register_okx_custom_data_is_idempotent() {
        register_okx_custom_data();
        register_okx_custom_data();
    }

    #[rstest]
    fn test_venue_option_greeks_to_option_greeks() {
        let instrument_id = InstrumentId::from("BTC-USD-240329-70000-C.OKX");
        let venue = VenueOptionGreeks {
            instrument_id,
            delta: 0.55,
            gamma: 0.02,
            vega: 0.15,
            theta: -0.05,
            rho: 0.01,
            mark_iv: 0.25,
            bid_iv: 0.24,
            ask_iv: 0.26,
            underlying_price: 70_500.0,
            open_interest: 1_000.0,
            convention: "PRICE_ADJUSTED".to_string(),
            ts_event: UnixNanos::from(1),
            ts_init: UnixNanos::from(2),
        };

        let greeks = venue.to_option_greeks();

        assert_eq!(greeks.instrument_id, instrument_id);
        assert_eq!(greeks.convention, GreeksConvention::PriceAdjusted);
        assert_eq!(greeks.delta, 0.55);
        assert_eq!(greeks.gamma, 0.02);
        assert_eq!(greeks.vega, 0.15);
        assert_eq!(greeks.theta, -0.05);
        assert_eq!(greeks.rho, 0.01);
        assert_eq!(greeks.mark_iv, Some(0.25));
        assert_eq!(greeks.bid_iv, Some(0.24));
        assert_eq!(greeks.ask_iv, Some(0.26));
        assert_eq!(greeks.underlying_price, Some(70_500.0));
        assert_eq!(greeks.open_interest, Some(1_000.0));
        assert_eq!(greeks.ts_event, UnixNanos::from(1));
        assert_eq!(greeks.ts_init, UnixNanos::from(2));
    }

    #[rstest]
    fn test_venue_option_greeks_from_option_greeks_into_custom_data() {
        let instrument_id = InstrumentId::from("BTC-USD-240329-70000-C.OKX");
        let greeks = OptionGreeks {
            instrument_id,
            convention: GreeksConvention::BlackScholes,
            greeks: OptionGreekValues {
                delta: 0.55,
                gamma: 0.02,
                vega: 0.15,
                theta: -0.05,
                rho: 0.01,
            },
            mark_iv: Some(0.25),
            bid_iv: Some(0.24),
            ask_iv: Some(0.26),
            underlying_price: Some(70_500.0),
            open_interest: None,
            ts_event: UnixNanos::from(1),
            ts_init: UnixNanos::from(2),
        };

        let venue = VenueOptionGreeks::from_option_greeks(&greeks);

        assert_eq!(venue.instrument_id, instrument_id);
        assert_eq!(venue.convention, "BLACK_SCHOLES");
        assert_eq!(venue.open_interest, 0.0);

        let custom = venue.into_custom_data();
        assert_eq!(custom.data_type.type_name(), "VenueOptionGreeks");
        assert_eq!(
            custom.data_type.identifier(),
            Some(instrument_id.to_string().as_str())
        );
    }

    #[rstest]
    fn test_venue_option_greeks_registers_standard_greeks_bridge() {
        let instrument_id = InstrumentId::from("BTC-USD-240329-70000-C.OKX");
        let venue = VenueOptionGreeks {
            instrument_id,
            delta: 0.55,
            gamma: 0.02,
            vega: 0.15,
            theta: -0.05,
            rho: 0.01,
            mark_iv: 0.25,
            bid_iv: 0.24,
            ask_iv: 0.26,
            underlying_price: 70_500.0,
            open_interest: 1_000.0,
            convention: "PRICE_ADJUSTED".to_string(),
            ts_event: UnixNanos::from(1),
            ts_init: UnixNanos::from(2),
        };
        let custom = venue.into_custom_data();

        register_okx_custom_data();
        let bridged = nautilus_model::data::custom_data_to_option_greeks(custom.data.as_ref())
            .expect("registered OKX venue greeks bridge");

        assert_eq!(bridged.instrument_id, instrument_id);
        assert_eq!(bridged.convention, GreeksConvention::PriceAdjusted);
        assert_eq!(bridged.delta, 0.55);
        assert_eq!(bridged.mark_iv, Some(0.25));
        assert_eq!(bridged.open_interest, Some(1_000.0));
    }

    #[cfg(feature = "arrow")]
    #[rstest]
    fn test_venue_option_greeks_arrow_schema() {
        use arrow::datatypes::DataType;
        use nautilus_serialization::arrow::ArrowSchemaProvider;

        let schema = VenueOptionGreeks::get_schema(None);

        assert_eq!(
            schema.field_with_name("instrument_id").unwrap().data_type(),
            &DataType::Utf8
        );
        assert_eq!(
            schema.field_with_name("delta").unwrap().data_type(),
            &DataType::Float64
        );
        assert_eq!(
            schema.field_with_name("gamma").unwrap().data_type(),
            &DataType::Float64
        );
        assert_eq!(
            schema.field_with_name("vega").unwrap().data_type(),
            &DataType::Float64
        );
        assert_eq!(
            schema.field_with_name("theta").unwrap().data_type(),
            &DataType::Float64
        );
        assert_eq!(
            schema.field_with_name("rho").unwrap().data_type(),
            &DataType::Float64
        );
        assert_eq!(
            schema.field_with_name("mark_iv").unwrap().data_type(),
            &DataType::Float64
        );
        assert_eq!(
            schema.field_with_name("bid_iv").unwrap().data_type(),
            &DataType::Float64
        );
        assert_eq!(
            schema.field_with_name("ask_iv").unwrap().data_type(),
            &DataType::Float64
        );
        assert_eq!(
            schema
                .field_with_name("underlying_price")
                .unwrap()
                .data_type(),
            &DataType::Float64,
        );
        assert_eq!(
            schema.field_with_name("open_interest").unwrap().data_type(),
            &DataType::Float64,
        );
        assert_eq!(
            schema.field_with_name("convention").unwrap().data_type(),
            &DataType::Utf8
        );
        assert_eq!(
            schema.field_with_name("ts_event").unwrap().data_type(),
            &DataType::UInt64
        );
        assert_eq!(
            schema.field_with_name("ts_init").unwrap().data_type(),
            &DataType::UInt64
        );
    }
}
