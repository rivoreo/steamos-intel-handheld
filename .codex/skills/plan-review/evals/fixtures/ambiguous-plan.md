# Feature Plan: Creator Tip Jar

## Problem

Creators want to receive tips from readers. Add a tipping feature so users can
send points to role card creators.

## Design Sketch

A "Tip" button on the role detail page opens a sheet with preset amounts.
Points transfer from the tipper's balance to the creator's balance. The
platform may take a cut — final percentage to be decided, and whether tipping
is limited to paid-tier users is still under discussion with product.

## Server

New `POST /tip/send` endpoint: validates balance, transfers points inside a
transaction, writes a `tip_record` row. Reuse the existing points ledger
service.

## Frontend

Desktop and mobile both add the Tip button and sheet. Amount presets: 10 / 50
/ 100 points plus custom input.

## Notes

We should make the experience feel rewarding for creators. Success will be
measured by whether creators feel more motivated.
