from os import listdir
from os.path import join, isfile, isdir, abspath
import shutil
import itertools

import numpy as np
import pandas as pd
import sklearn as sk
from sklearn.metrics import mean_squared_error, mean_absolute_error
# import statsmodels.api as sm
# import matplotlib.pyplot as plt
# import seaborn as sns

from pymatgen.analysis.functional_groups import FunctionalGroupExtractor
from pymatgen.io.babel import BabelMolAdaptor

from atomate.qchem.database import QChemCalcDb

from moltherm.compute.drones import MolThermDrone
from moltherm.compute.outputs import QCOutput
from moltherm.compute.utils import get_molecule, extract_id, associate_qchem_to_mol

__author__ = "Evan Spotte-Smith"
__version__ = "0.1"
__maintainer__ = "Evan Spotte-Smith"
__email__ = "espottesmith@gmail.com"
__status__ = "Alpha"
__date__ = "July 2018"


class MolThermDataProcessor:
    """
    This class can be used to extract data from MolThermWorkflow workflows,
    including extracting thermo data from calculations and generating predicted
    boiling and melting points.
    """

    def __init__(self, base_dir, reactant_pre="rct_", product_pre="pro_",
                 db_file="db.json"):
        """
        :param base_dir: Directory where input and output data should be stored.
        :param reactant_pre: Prefix for reactant files.
        :param product_pre: Prefix for product files.
        :param db_file: Path to database config file.
        """

        self.base_dir = base_dir
        self.reactant_pre = reactant_pre
        self.product_pre = product_pre
        self.db_file = db_file

        try:
            self.db = QChemCalcDb.from_db_file(self.db_file)
        except:
            self.db = None

    def quick_check(self, dirs):
        """
        Returns only those reactions which have appropriate products and
        reactants (products, reactants have same number of atoms).

        This is not a sophisticated checking mechanism, and could probably be
        easily improved upon.

        :return:
        """

        add_up = []

        for d in dirs:
            path = join(self.base_dir, d)
            files = [f for f in listdir(path) if isfile(join(path, f))]
            rcts = [f for f in files if f.startswith(self.reactant_pre) and f.endswith(".mol")]
            pros = [f for f in files if f.startswith(self.product_pre) and f.endswith(".mol")]

            rct_mols = [get_molecule(join(self.base_dir, d, r)) for r in rcts]
            pro_mols = [get_molecule(join(self.base_dir, d, p)) for p in pros]

            total_pro_length = sum([len(p) for p in pro_mols])
            total_rct_length = sum([len(r) for r in rct_mols])

            if total_pro_length == total_rct_length:
                add_up.append(d)

        return add_up

    def extract_reaction_thermo_files(self, path):
        """
        Naively scrape thermo data from QChem output files.

        :param path: Path to a subdirectory.

        :return: dict {prop: value}, where properties are enthalpy, entropy.
        """

        base_path = join(self.base_dir, path)

        rct_ids = [extract_id(f) for f in listdir(base_path) if
                   f.endswith(".mol") and f.startswith(self.reactant_pre)]

        pro_ids = [extract_id(f) for f in listdir(base_path) if
                   f.endswith(".mol") and f.startswith(self.product_pre)]

        rct_map = {m: [f for f in listdir(base_path) if
                       f.startswith(self.reactant_pre) and m in f
                       and ".out" in f and not f.endswith("_copy")]
                   for m in rct_ids}
        pro_map = {m: [f for f in listdir(base_path)
                       if f.startswith(self.product_pre) and m in f
                       and ".out" in f] for m in pro_ids}

        rct_thermo = {"enthalpy": 0, "entropy": 0, "energy": 0, "has_sp": {}}
        pro_thermo = {"enthalpy": 0, "entropy": 0, "energy": 0, "has_sp": {}}

        for mol in rct_map.keys():
            enthalpy = 0
            entropy = 0
            energy_opt = 0
            energy_sp = 0

            for out in rct_map[mol]:
                qcout = QCOutput(join(base_path, out))

                # Catch potential for Nonetype entries
                if "freq" in out:
                    enthalpy = qcout.data.get("enthalpy", 0) or 0
                    entropy = qcout.data.get("entropy", 0) or 0
                elif "opt" in out:
                    energy_opt = qcout.data.get("final_energy", 0) or 0
                elif "sp" in out:
                    energy_sp = qcout.data.get("final_energy_sp", 0) or 0

            if energy_sp == 0:
                rct_thermo["energy"] += energy_opt
                rct_thermo["has_sp"][self.reactant_pre + str(mol)] = False
            else:
                rct_thermo["energy"] += energy_sp
                rct_thermo["has_sp"][self.reactant_pre + str(mol)] = True

            rct_thermo["enthalpy"] += enthalpy
            rct_thermo["entropy"] += entropy
            print(path, mol, enthalpy, energy_sp)

        for mol in pro_map.keys():
            enthalpy = 0
            entropy = 0
            energy_opt = 0
            energy_sp = 0

            for out in pro_map[mol]:
                qcout = QCOutput(join(base_path, out))

                # Catch potential for Nonetype entries
                if "freq" in out:
                    enthalpy = qcout.data.get("enthalpy", 0) or 0
                    entropy = qcout.data.get("entropy", 0) or 0
                elif "opt" in out:
                    energy_opt = qcout.data.get("final_energy", 0) or 0
                elif "sp" in out:
                    energy_sp = qcout.data.get("final_energy_sp", 0) or 0

            # Enthalpy calculation should actually be enthalpy - energy_sp
            # But currently, not all calculations have sp
            if int(energy_sp) == 0:
                pro_thermo["energy"] += energy_opt
                pro_thermo["has_sp"][self.product_pre + str(mol)] = False
            else:
                pro_thermo["energy"] += energy_sp
                pro_thermo["has_sp"][self.product_pre + str(mol)] = True

            pro_thermo["enthalpy"] += enthalpy
            pro_thermo["entropy"] += entropy
            print(path, mol, enthalpy, energy_sp)

        thermo_data = {}

        # Generate totals as ∆H = H_pro - H_rct, ∆S = S_pro - S_rct
        # Also ensures that units are appropriate (Joules/mol,
        # rather than cal/mol or kcal/mol, or hartree for energy)
        energy = (pro_thermo["energy"] - rct_thermo["energy"]) * 627.509
        enthalpy = (pro_thermo["enthalpy"] - rct_thermo["enthalpy"])
        print(path, energy, enthalpy)
        thermo_data["enthalpy"] = (energy + enthalpy) * 1000 * 4.184
        thermo_data["entropy"] = (pro_thermo["entropy"] - rct_thermo["entropy"]) * 4.184
        try:
            thermo_data["t_critical"] = thermo_data["enthalpy"] / thermo_data["entropy"]
        except ZeroDivisionError:
            thermo_data["t_critical"] = None
        # Combine dicts from pro_thermo and rct_thermo
        thermo_data["has_sp"] = {**pro_thermo["has_sp"], **rct_thermo["has_sp"]}

        result = {"thermo": thermo_data,
                  "directory": path,
                  "reactant_ids": rct_ids,
                  "product_ids": pro_ids}

        return result

    def extract_reaction_thermo_db(self, directory, opt=None, freq=None, sp=None):
        """
        Gathers all relevant reaction parameters, including references to
        each job performed.

        :param directory: Directory name where the reaction is stored. Right
            now, this is the easiest way to identify the reaction. In the
            future, more sophisticated searching should be used.
        :param opt: dict containing information about the optimization jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.
        :param freq: dict containing information about the frequency jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.
        :param sp: dict containing information about the single-point jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.

        :return: dict
        """

        if self.db is None:
            raise RuntimeError("Could not connect to database. Check db_file"
                               "and try again later.")

        # To extract enthalpy and entropy from calculation results
        # Note: After all sp jobs are finished, it should be unnecessary to use
        # energy_opt
        def get_thermo(job):
            enthalpy = 0
            entropy = 0
            energy_sp = 0

            for calc in job["calcs_reversed"]:
                if calc["task"]["type"] == "freq" or calc["task"]["type"] == "frequency":
                    enthalpy = calc["enthalpy"]
                    entropy = calc["entropy"]
                if calc["task"]["type"] == "sp":
                    energy_sp = calc["final_energy_sp"]

            return {"enthalpy": enthalpy,
                    "entropy": entropy,
                    "energy": energy_sp}

        if abspath(directory) != directory:
            directory = join(self.base_dir, directory)

        mol_files = [f for f in listdir(directory) if f.endswith(".mol")]

        dir_ids = [extract_id(f) for f in mol_files]

        collection = self.db.db["molecules"]
        records = []

        for mol_id in dir_ids:
            record = collection.find_one({"mol_id": str(mol_id)})
            records.append(record)

        # Sort files for if they are reactants or products
        reactants = []
        products = []
        for i, record in enumerate(records):
            filename = mol_files[i]
            if opt is None:
                for calc in record["calcs_reversed"]:
                    if calc["task"]["type"] == "opt" or \
                            calc["task"]["type"] == "optimization":
                        method = calc["input"]["rem"]["method"]
                        basis = calc["input"]["rem"]["basis"]
                        solvent_method = calc["input"]["rem"].get(
                            "solvent_method", None)
                        if solvent_method == "smd":
                            if calc["input"]["smx"] is None:
                                solvent = None
                            else:
                                solvent = calc["input"]["smx"]["solvent"]
                        elif solvent_method == "pcm":
                            solvent = calc["input"]["solvent"]
                        else:
                            solvent = None

                        opt = {"method": method,
                               "basis": basis,
                               "solvent_method": solvent_method,
                               "solvent": solvent}
                        break
            if freq is None:
                for calc in record["calcs_reversed"]:
                    if calc["task"]["type"] == "freq" or \
                            calc["task"]["type"] == "frequency":
                        method = calc["input"]["rem"]["method"]
                        basis = calc["input"]["rem"]["basis"]
                        solvent_method = calc["input"]["rem"].get(
                            "solvent_method", None)
                        if solvent_method == "smd":
                            if calc["input"]["smx"] is None:
                                solvent = None
                            else:
                                solvent = calc["input"]["smx"]["solvent"]
                        elif solvent_method == "pcm":
                            solvent = calc["input"]["solvent"]
                        else:
                            solvent = None

                        freq = {"method": method,
                                "basis": basis,
                                "solvent_method": solvent_method,
                                "solvent": solvent}
                        break
            if sp is None:
                for calc in record["calcs_reversed"]:
                    if calc["task"]["type"] == "sp":
                        method = calc["input"]["rem"]["method"]
                        basis = calc["input"]["rem"]["basis"]
                        solvent_method = calc["input"]["rem"].get(
                            "solvent_method", None)
                        if solvent_method == "smd":
                            if calc["input"]["smx"] is None:
                                solvent = None
                            else:
                                solvent = calc["input"]["smx"]["solvent"]
                        elif solvent_method == "pcm":
                            solvent = calc["input"]["solvent"]
                        else:
                            solvent = None

                        sp = {"method": method,
                              "basis": basis,
                              "solvent_method": solvent_method,
                              "solvent": solvent}
                        break

            if filename.startswith(self.reactant_pre):
                reactants.append(record)
            elif filename.startswith(self.product_pre):
                products.append(record)
            else:
                print("Skipping {} because it cannot be determined if it is"
                      "reactant or product.".format(filename))
                continue

        # Get ids
        reactant_ids = [r["mol_id"] for r in reactants]
        product_ids = [p["mol_id"] for p in products]

        # Get thermo data
        rct_thermo = [get_thermo(r) for r in reactants]
        pro_thermo = [get_thermo(p) for p in products]

        # Compile reaction thermo from reactant and product thermos
        delta_e = sum(p["energy"] for p in pro_thermo) - sum(r["energy"] for r in rct_thermo)
        delta_e *= 627.509
        delta_h = sum(p["enthalpy"] for p in pro_thermo) - sum(r["enthalpy"] for r in rct_thermo) + delta_e
        delta_h *= 1000 * 4.184
        delta_s = sum(p["entropy"] for p in pro_thermo) - sum(r["entropy"] for r in rct_thermo)
        delta_s *= 4.184
        thermo = {
            "enthalpy": delta_h,
            "entropy": delta_s
        }

        try:
            thermo["t_critical"] = delta_h / delta_s
        except ZeroDivisionError:
            thermo["t_critical"] = 0

        result = {"dir_name": directory,
                  "opt": opt,
                  "freq": freq,
                  "sp": sp,
                  "reactant_ids": reactant_ids,
                  "product_ids": product_ids,
                  "thermo": thermo}

        return result

    def record_molecule_data_db(self, mol_id, calc_dir, input_file, output_file):
        """
        Compile calculation information for a single molecule and record it in
        the molecules collection.

        :param mol_id: Unique identifier for molecule (str)
        :param calc_dir: Directory where molecule information is stored.
        :param input_file: Basic format for input files. The Drone which
            compiles the molecule information will use this to pattern-match
            files.
        :param output_file: Basic format for output files. The Drone which
            compiles the molecule information will use this to pattern-match
            files.
        :return:
        """

        drone = MolThermDrone()

        task_doc = drone.assimilate(
            path=calc_dir,
            input_file=input_file,
            output_file=output_file,
            multirun=False)

        task_doc["mol_id"] = mol_id

        if self.db is None:
            raise RuntimeError("Cannot record data to db without valid database"
                               " connection!")

        collection = self.db.db["molecules"]

        collection.insert_one(task_doc)

    def record_reaction_data_db(self, directory, use_files=True, use_db=False,
                                opt=None, freq=None, sp=None):
        """
        Record thermo data in thermo collection.

        :param directory: Directory name where the reaction is stored. Right
            now, this is the easiest way to identify the reaction. In the
            future, more sophisticated searching should be used.
        :param use_files: If set to True (default True), use
            get_reaction_thermo_files to gather data
        :param use_db: If set to True (default False), use
            extract_reaction_data to gather data
        :param opt: dict containing information about the optimization jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.
        :param freq: dict containing information about the frequency jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.
        :param sp: dict containing information about the single-point jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.

        :return:
        """

        if self.db is None:
            raise RuntimeError("Could not connect to database. Check db_file"
                               "and try again later.")

        collection = self.db.db["thermo"]

        if use_db:
            collection.insert_one(self.extract_reaction_thermo_db(directory, opt=opt,
                                                         freq=freq, sp=sp))
        elif use_files:
            collection.insert_one(self.extract_reaction_thermo_files(directory))
        else:
            raise RuntimeError("Either database or files must be used to "
                               "extract thermo data.")

    def record_reaction_data_file(self, directory, filename="thermo.txt",
                                  use_files=True, use_db=False, opt=None,
                                  freq=None, sp=None):
        """
        Record thermo data in thermo.txt file.

        Note: This function does NOT store the reactant and product IDs

        :param directory: Directory name where the reaction is stored. Right
            now, this is the easiest way to identify the reaction. In the
            future, more sophisticated searching should be used.
        :param filename: File (within directory) where data should be stored.
            By default, it will be stored in thermo.txt.
        :param use_files: If set to True (default True), use
            get_reaction_thermo_files to gather data
        :param use_db: If set to True (default False), use
            extract_reaction_data to gather data
        :param opt: dict containing information about the optimization jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.
        :param freq: dict containing information about the frequency jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.
        :param sp: dict containing information about the single-point jobs. By
            default, this is None, and that information will be obtained by
            querying the self.db.tasks collection.

        :return:
        """

        if abspath(directory) != directory:
            directory = join(self.base_dir, directory)

        with open(join(directory, filename), "w+") as file:
            if use_db:
                data = self.extract_reaction_data(directory, opt=opt, freq=freq,
                                                  sp=sp)
            elif use_files:
                data = self.get_reaction_thermo_files(directory)
            else:
                raise RuntimeError("Either database or files must be used to "
                                   "extract thermo data.")

            file.write("Directory: {}\n".format(data["dir_name"]))
            file.write("Optimization Input: {}\n".format(data.get("opt", "")))
            file.write("Frequency Input: {}\n".format(data.get("freq", "")))
            file.write("Single-Point Input: {}\n".format(data.get("sp", "")))
            file.write("Reaction Enthalpy: {}\n".format(data["thermo"]["enthalpy"]))
            file.write("Reaction Entropy: {}\n".format(data["thermo"]["entropy"]))
            file.write("Critical/Switching Temperature: {}\n".format(data["thermo"]["t_critical"]))

    def copy_outputs_across_directories(self):
        """
        Copy output files between subdirectories to ensure that all reaction
        directories that need outputs of a given molecule will have them.

        Note: This function should not be used unless necessary. It was written
        because for each directory, only a single database entry was being made
        (because db entries were being overwritten by default.

        :return:
        """

        files_copied = 0

        dirs = [d for d in listdir(self.base_dir) if isdir(join(self.base_dir, d)) and not d.startswith("block")]
        print("Number of directories: {}".format(len(dirs)))

        for start_d in dirs:
            start_p = join(self.base_dir, start_d)
            mol_files = [f for f in listdir(start_p) if isfile(join(start_p, f)) and f.endswith(".mol")]
            out_files = [f for f in listdir(start_p) if isfile(join(start_p, f)) and ".out" in f]

            for mf in mol_files:
                is_covered = False
                mol_id = extract_id(mf)

                mol_obj = get_molecule(join(start_p, mf))

                for out in out_files:
                    qcout = QCOutput(join(start_p, out))
                    if sorted(qcout.data["initial_molecule"].species) == sorted(mol_obj.species):
                        # If there is already output, do not copy any files
                        is_covered = True

                if is_covered:
                    continue

                for other_d in dirs:
                    if other_d == start_d:
                        continue
                    if is_covered:
                        break

                    other_p = join(self.base_dir, other_d)
                    # Check if this id is present
                    other_mol_files = [f for f in listdir(other_p) if isfile(join(other_p, f)) and f.endswith(".mol") and mol_id in f]
                    other_out_files = [f for f in listdir(other_p) if isfile(join(other_p, f)) and ".out" in f]
                    to_copy = []
                    for other_mol in other_mol_files:
                        if other_mol.startswith(self.product_pre):
                            to_copy = [f for f in other_out_files if
                                       f.startswith(self.product_pre)]
                        elif other_mol.startswith(self.reactant_pre):
                            to_check = [f for f in other_out_files if f.startswith(self.reactant_pre)]
                            to_copy = []
                            for file in to_check:
                                qcout = QCOutput(join(other_p, file))
                                if qcout.data["initial_molecule"].species == mol_obj.species:
                                    to_copy.append(file)
                        else:
                            to_copy = []
                    for file in to_copy:
                        shutil.copyfile(join(other_p, file), join(start_p, file + "_copy"))
                        files_copied += 1

                    if files_copied > 0:
                        is_covered = True
        print("Number of files copied: {}".format(files_copied))

    def find_common_reactants(self, rct_id):
        """
        Searches all subdirectories for those that have reactant .mol files with
        unique id rct_id.

        :param rct_id: String representing unique identifier for Reaxys
            molecules.
        :return: List of reaction directories containing the given reactant.
        """
        results = []
        for d in listdir(self.base_dir):
            if isdir(join(self.base_dir, d)) and not d.startswith("block"):
                for f in listdir(join(self.base_dir, d)):
                    if rct_id in f:
                        results.append(d)
        return results

    def map_reactants_to_reactions(self):
        """
        Construct a dict showing which directories share each reactant.

        This is useful for analysis of common reactants, and to identify the
        "source" of a given reactant (in which directory the calculation
        actually took place).

        :return:
        """

        mapping = {}
        dirs = [d for d in listdir(self.base_dir)
                if isdir(join(self.base_dir, d)) and not d.startswith("block")]

        for d in dirs:
            if isdir(join(self.base_dir, d)) and not d.startswith("block"):
                molfiles = [f for f in listdir(join(self.base_dir, d))
                            if f.endswith(".mol")
                            and f.startswith(self.reactant_pre)]
                for file in molfiles:
                    f_id = extract_id(file)
                    if f_id in mapping:
                        mapping[f_id].append(d)
                    else:
                        mapping[f_id] = [d]

        return mapping

    def get_completed_molecules(self, dirs=None, extra=False):
        """
        Returns a list of molecules with completed opt, freq, and sp output
        files.

        :param dirs: List of directories to search for completed molecules.
        :params extra: If True, include directory of completed reaction and name
            of molfile along with mol_id
        :return: set of completed molecules
        """

        completed = set()

        all_dirs = [d for d in listdir(self.base_dir)
                    if isdir(join(self.base_dir, d)) and not d.startswith("block")]

        if dirs is not None:
            all_dirs = [d for d in all_dirs if d in dirs]

        for d in all_dirs:
            path = join(self.base_dir, d)
            mapping = associate_qchem_to_mol(self.base_dir, d)

            for molfile, qcfiles in mapping.items():
                mol_id = extract_id(molfile)

                for outfile in qcfiles["out"]:
                    if "sp" in outfile:
                        spfile = QCOutput(join(path, outfile))

                        completion = spfile.data.get("completion", False)

                        # Currently will catch iefpcm or smd
                        if completion:
                            if extra:
                                completed.add((d, molfile, mol_id))
                            else:
                                completed.add(mol_id)

        return completed

    def get_completed_reactions(self):
        """
        Returns a list of directories (reactions) where all molecules are
        completed.

        :return: list of directories with complete information.
        """

        if self.db is None:
            raise RuntimeError("Could not connect to database. Check db_file"
                               "and try again later.")

        collection = self.db.db["molecules"]

        completed_molecules = [x["mol_id"] for x in collection.find()]

        completed_reactions = set()

        dirs = [d for d in listdir(self.base_dir) if isdir(join(self.base_dir, d)) and not d.startswith("block")]

        for d in dirs:
            path = join(self.base_dir, d)

            mols = [extract_id(f) for f in listdir(path) if isfile(join(path, f)) and f.endswith(".mol")]

            are_completed = [True if m in completed_molecules else False for m in mols]

            if all(are_completed):
                completed_reactions.add(d)

        return completed_reactions

    def get_molecule_data(self, mol_id):
        """
        Compile all useful molecular data for analysis, including molecule size
        (number of atoms), molecular weight, enthalpy, entropy, and functional
        groups.

        NOTE: This function automatically converts energy, enthalpy, and entropy
        into SI units (J/mol and J/mol*K)

        :param mol_id: Unique ID associated with the molecule.
        :return: dict of relevant molecule data.
        """

        mol_data = {"id": mol_id}

        if self.db is None:
            raise RuntimeError("Cannot query database; connection is invalid."
                               " Try to connect again.")

        collection = self.db.db["molecules"]

        mol_entry = collection.find_one({"mol_id": mol_id})

        for calc in mol_entry["calcs_reversed"]:
            if calc["task"]["name"] in ["freq", "frequency"]:
                mol_data["enthalpy"] = calc["enthalpy"] * 4.184 * 1000
                mol_data["entropy"] = calc["entropy"] * 4.184
            if calc["task"]["name"] == "sp":
                mol_data["energy"] = calc["final_energy_sp"] * 627.509 * 4.184 * 1000
            if calc["task"]["name"] in ["opt", "optimization"]:
                mol_data["molecule"] = calc["molecule_from_optimized_geometry"]

        adaptor = BabelMolAdaptor(mol_data["molecule"])
        pbmol = adaptor.pybel_mol

        mol_data["number_atoms"] = len(mol_data["molecule"])
        mol_data["molecular_weight"] = pbmol.molwt
        mol_data["surface_area"] = pbmol.data["Surface Area"]
        # This might not be efficient
        mol_data["tpsa"] = pbmol.calcdesc()["TPSA"]

        extractor = FunctionalGroupExtractor(mol_data["molecule"])
        func_grps = extractor.get_all_functional_groups()

        mol_data["functional_groups"] = extractor.categorize_functional_groups(func_grps)

        return mol_data

    def get_reaction_data(self, directory=None, mol_ids=None):
        """
        Compile all useful data for a set of molecules associated with a
        particular reaction. This data will be compiled on a reaction basis
        (difference between reactants and products) as well as an individual
        molecule basis.

        :param directory: Subdirectory where molecule data is located.
        :param mol_ids: List of unique IDs for molecules associated with the
            reaction
        :return: dict of relevant reaction data.
        """

        reaction_data = {}

        collection = self.db.db["molecules"]

        if directory is not None:
            entries = collection.find({"dir_name": join(self.base_dir, directory)})
            mol_ids = [extract_id(e["task_label"]) for e in entries]

            component_data = [self.get_molecule_data(m) for m in mol_ids]

        elif mol_ids is not None:
            component_data = [self.get_molecule_data(m) for m in mol_ids]

        else:
            raise ValueError("get_reaction_data requires either a directory or "
                             "a set of molecule ids.")

        component_data = sorted(component_data, key=lambda x: len(x["molecule"]))

        reaction_data["directory"] = directory
        reaction_data["mol_ids"] = mol_ids
        reaction_data["product"] = component_data[-1]
        reaction_data["reactants"] = component_data[:-1]

        return reaction_data


class MolThermAnalyzer:
    """
    This class performs analysis based on the data obtained from
    MolThermWorkflow and extracted via MolThermDataProcessor.
    """

    def __init__(self, dataset, setup=True, in_features=None, dep_features=None,
                 func_groups=None):
        """
        :param dataset: A list of dicts representing all data necessary to
            represent a reaction.
        :param setup: If True (default), then clean the data (put it in a format
            that is appropriate for analysis).
        :param in_features: List of feature/descriptor names for independent
            variables (number_atoms, molecular_weight, etc.).
        :param dep_features: List of feature/descriptor names for dependent
            variables (enthalpy, entropy)
        """

        if in_features is None:
           self.in_features = ["number_atoms", "molecular_weight",
                               "surface_area", "tpsa"]
        else:
            self.in_features = in_features

        if dep_features is None:
            self.dep_features = ["enthalpy", "entropy", "t_star"]
        else:
            self.dep_features = dep_features

        if func_groups is None:
            if setup:
                self.func_groups = self._setup_func_groups(dataset)
            else:
                self.func_groups = np.arange(len(dataset["reactants"]["functional_groups"][0]))
        else:
            self.func_groups = func_groups

        if setup:
            self.dataset = self._setup_dataset(dataset)
        else:
            self.dataset = dataset

    def _setup_func_groups(self, dataset):
        """
        Construct a numpy array with labels corresponding to each functional
        group that appears in the dataset.

        :param dataset: list of dicts representing all data necessary to
            represent a reaction.
        :return: np.ndarray
        """

        func_groups = {}

        for datapoint in dataset:
            molecules = [datapoint["product"]] + datapoint["reactants"]
            for molecule in molecules:
                func_groups.update(molecule)

        return np.array(list(func_groups.keys()))

    def _setup_dataset(self, dataset):
        """
        Alter dataset to make it appropriate for analysis.

        :param dataset: list of dicts, with each dict representing a reaction
        :return: dict containing individual molecule information as well as
            overall reaction information.
        """

        new_dset = {"molecules": {}, "reactions": {}}

        all_molecules = []

        num_reactions = len(dataset)

        for datapoint in dataset:
            if datapoint["product"] not in all_molecules:
                all_molecules.append(datapoint["product"])

            for rct in datapoint["reactants"]:
                if rct not in all_molecules:
                    all_molecules.append(rct)

        new_dset["molecules"]["ids"] = np.array([m["id"] for m in all_molecules])
        new_dset["reactions"]["ids"] = np.array([p["mol_ids"] for p in dataset])
        new_dset["reactions"]["dirs"] = np.array([p["directory"] for p in dataset])

        num_molecules = len(all_molecules)

        # Vectorize molecule and reaction features, including thermodynamic
        # properties, surface area, etc.
        for marker in (self.in_features + self.dep_features):
            new_dset["molecules"][marker] = np.zeros(num_molecules)

            for i, mol in enumerate(all_molecules):
                if marker == "enthalpy":
                    new_dset["molecules"][marker][i] = mol["enthalpy"] + mol["energy"]
                else:
                    new_dset["molecules"][marker][i] = mol[marker]

            new_dset["reactions"][marker] = np.zeros(num_reactions)

            for i, react in enumerate(dataset):
                if marker == "enthalpy":
                    pro_data = react["product"]["enthalpy"] + react["product"]["energy"]
                    rct_data = sum(r["enthalpy"] + r["energy"]
                                   for r in react["reactants"])
                else:
                    pro_data = react["product"][marker]
                    rct_data = sum(r[marker] for r in react["reactants"])

                new_dset["reactions"][marker][i] = pro_data - rct_data

        new_dset["molecules"]["functional_groups"] = np.zeros((num_molecules,
                                                               len(self.func_groups)))
        # Vectorize functional groups
        for i, mol in enumerate(all_molecules):
            for j, grp in enumerate(self.func_groups):
                if grp in mol["functional_groups"].keys():
                    new_dset["molecules"]["functional_groups"][i, j] = mol["functional_groups"][grp]["count"]

        new_dset["reactions"]["functional_groups"] = np.zeros((num_reactions,
                                                               len(self.func_groups)))
        for i, react in enumerate(dataset):
            pro_grps = np.zeros(len(self.func_groups))
            rct_grps = np.zeros(len(self.func_groups))

            for j, grp in enumerate(self.func_groups):
                if grp in react["product"]["functional_groups"].keys():
                    pro_grps[j] = react["product"]["functional_groups"][grp]["count"]

                for mol in react["reactants"]:
                    if grp in mol["functional_groups"].keys():
                        rct_grps += mol["functional_groups"][grp]["count"]

            new_dset["reactions"]["functional_groups"][i] = pro_grps - rct_grps

        return new_dset

    def analyze_functional_groups(self, dep_feature, molecules=False):
        """
        Perform a regression analysis to determine the effect of various
        functional groups on a particular dependent feature (for instance,
        enthalpy)

        :param dep_feature: str representing a dependent variable to be
            analyzed
        :param molecules: If True, perform analysis on an individual molecule
            basis, rather than on a reaction basis
        :return: dict of statistical values
        """

        if molecules:
            in_frame = pd.DataFrame(self.dataset["molecules"]["functional_groups"],
                                    columns=self.func_groups)
            dep_frame = pd.DataFrame(self.dataset["molecules"][dep_feature],
                                     columns=[dep_feature])
        else:
            in_frame = pd.DataFrame(self.dataset["reactions"]["functional_groups"],
                                    columns=self.func_groups)
            dep_frame = pd.DataFrame(self.dataset["reactions"][dep_feature],
                                     columns=[dep_feature])

        lm = sk.linear_model.LinearRegression()
        lm.fit(in_frame, dep_frame)

        score = lm.score(in_frame, dep_frame)
        coefficients = lm.coef_
        intercept = lm.intercept_

        return {"r_squared": score,
                "coefficients": coefficients,
                "intercept": intercept}

    def analyze_features(self, in_features, dep_feature, molecules=False):
        """
        Perform a regression analysis to determine the effect of various
        parameters (molecular weight, for instance) on a particular dependent
        feature (for instance, enthalpy)

        :param in_features: list of strs representing independent variables to
            be analyzed
        :param dep_feature: str representing a dependent variable to be
            analyzed
        :param molecules: If True, perform analysis on an individual molecule
            basis, rather than on a reaction basis
        :return: dict of statistical values
        """

        if molecules:
            in_dataset = {feat: self.dataset["molecules"][feat] for feat in
                          in_features}
            in_frame = pd.DataFrame(data=in_dataset)
            dep_frame = pd.DataFrame(self.dataset["molecules"][dep_feature],
                                     columns=[dep_feature])

        else:
            in_dataset = {feat: self.dataset["reactions"][feat] for feat in
                          in_features}
            in_frame = pd.DataFrame(data=in_dataset)
            dep_frame = pd.DataFrame(self.dataset["molecules"][dep_feature],
                                     columns=[dep_feature])

        lm = sk.linear_model.LinearRegression()
        lm.fit(in_frame, dep_frame)

        score = lm.score(in_frame, dep_frame)
        coefficients = lm.coef_
        intercept = lm.intercept_

        return {"r_squared": score,
                "coefficients": coefficients,
                "intercept": intercept}

    # def plot_relation_functional_group(self, group, dep_feature, molecules=False):
    #     """
    #     Plot some dependent feature (enthalpy, entropy, etc.) versus counts of
    #     a single functional group.
    #
    #     :param group: Functional group to be plotted. Must be a member of
    #         self.func_groups
    #     :param dep_feature: Dependent feature to be evaluated. Must be a member
    #         of self.dep_features
    #     :param molecules: If true, plot on an individual molecule basis, rather
    #         than on a reaction basis
    #     :return:
    #     """
    #
    #     sns.set(style="ticks", color_codes=True)
    #
    #     col = self.func_groups.index(group)
    #
    #     if molecules:
    #         group_data = self.dataset["molecules"]["functional_groups"][:, col]
    #         dep_data = self.dataset["molecules"][dep_feature]
    #     else:
    #         group_data = self.dataset["reactions"]["functional_groups"][:, col]
    #         dep_data = self.dataset["reactions"][dep_feature]
    #
    #     dframe = pd.DataFrame(data={group: group_data, dep_feature: dep_data})
    #
    #     sns.catplot(x=group, y=dep_feature, data=dframe)
    #
    # def plot_relation(self, in_feature, dep_feature, molecules=False):
    #     """
    #
    #     :param in_feature: Independent feature to be evaluated. Must be a member
    #         of self.in_features
    #     :param dep_feature: Dependent feature to be evaluated. Must be a member
    #         of self.dep_features
    #     :param molecules: If true, plot on an individual molecule basis, rather
    #         than on a reaction basis
    #     :return:
    #     """
    #
    #     sns.set(style="ticks", color_codes=True)
    #
    #     if molecules:
    #         in_data = self.dataset["molecules"][in_feature]
    #         dep_data = self.dataset["molecules"][dep_feature]
    #     else:
    #         in_data = self.dataset["reactions"][in_feature]
    #         dep_data = self.dataset["reactions"][dep_feature]
    #
    #     dframe = pd.DataFrame(data={in_feature: in_data, dep_feature: dep_data})
    #
    #     sns.catplot(x=in_feature, y=dep_feature, data=dframe)
