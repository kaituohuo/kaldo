# get_reference.sh is used to 
# obtain reference calculations
#  for example 2

# Fetch precalculated forceconstant folder from remote
wget http://sophe.ucdavis.edu/structures/reference/amorphous_silicon_Tersoff_LAMMPS.tar.gz
# Untar precalculated files and clean up 
tar xzvf amorphous_silicon_Tersoff_LAMMPS.tar.gz
rm -rf amorphous_silicon_Tersoff_LAMMPS.tar.gz
echo  "  "
echo "Reference calculation files are obtained."
