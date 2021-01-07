#~/usr/bin/env bash
set -e

DATABASE_DIR=database

copy_databases () {
  db=$1
  # Copy to *_test directory
  altered=$DATABASE_DIR/${db}_test
  cp -r "$DATABASE_DIR/$db" "$altered"

  # Rename .sqlite files
  cd "$altered"
  for f in ${db}*.sqlite
  do
    mv "$f" "${db}_test${f#${db}}"
  done
  cd ../..
}

alter_yelp () {
  for f in "$DATABASE_DIR/yelp_test"/*.sqlite
  do
    echo "ALTER TABLE neighbourhood RENAME TO neighborhood" | sqlite3 "$f"
    echo "ALTER TABLE neighborhood RENAME COLUMN neighbourhood_name TO neighborhood_name" | sqlite3 "$f"
  done
}

alter_imdb () {
  for f in "$DATABASE_DIR/imdb_test"/*.sqlite
  do
    echo "ALTER TABLE cast RENAME TO cast2" | sqlite3 "$f"
  done
}


for DB in "imdb" "yelp"
do
  echo $DB
  if [ ! -d "$DATABASE_DIR/${DB}_test" ]
  then
    copy_databases $DB
    alter_"$DB"
  else
    echo "$DATABASE_DIR/${DB}_test already exists"
  fi
done
